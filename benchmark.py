#!/usr/bin/env python3
"""Standard-library CLM / Jev System One comparison runner.

API: POST /v1/systemone {model,state,questions:{decision:{type,instructions,criteria?}}}
Outputs per-case JSONL and per-model / comparative JSON, CSV, Markdown, HTML reports.
No network calls are made by report, validate or demo.
"""
import argparse, collections, concurrent.futures, csv, datetime, hashlib, html
import json, math, os, random, statistics, subprocess, sys, threading, time
import urllib.error, urllib.request
from pathlib import Path

QID='decision'
RESULT_FIELDS=['id','category','language','question_type','gold','prediction','correct','confidence',
               'latency_ms','server_latency_ms','input_tokens','output_tokens','run_index','error','probabilities']


def read_jsonl(path):
    result=[]
    with Path(path).open(encoding='utf-8') as fp:
        for line_no,line in enumerate(fp,1):
            if not line.strip(): continue
            try: result.append(json.loads(line))
            except json.JSONDecodeError as exc: raise ValueError(f'{path}:{line_no}: {exc}') from exc
    return result


def validate_dataset(path):
    data=read_jsonl(path)
    ids=set(); counts=collections.Counter()
    for index,item in enumerate(data,1):
        need=('id','state','question','gold','category','language')
        if any(k not in item for k in need): raise ValueError(f'Case {index}: missing required fields')
        cid=item['id']; kind=item['question'].get('type')
        if cid in ids: raise ValueError(f'Duplicate id: {cid}')
        ids.add(cid); counts[kind]+=1
        if kind=='choice' and item['gold'] not in item['question'].get('criteria',{}): raise ValueError(f'Bad choice gold {cid}')
        if kind=='score' and not 0<=int(item['gold'])<len(item['question'].get('criteria',[])): raise ValueError(f'Bad score gold {cid}')
        if kind=='noul' and item['gold'] not in [0,1]: raise ValueError(f'Bad noul gold {cid}')
        if kind not in {'choice','score','noul'}: raise ValueError(f'Bad type {cid}')
    return data,counts


def endpoint_url(value):
    base=value.rstrip('/')
    if base.endswith('/v1/systemone'): return base
    if base.endswith('/v1'): return base+'/systemone'
    return base+'/v1/systemone'


def call_api(row,endpoint,model,key,timeout,retries):
    payload={'model':model,'state':row['state'],'questions':{QID:row['question']}}
    body=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    headers={'Content-Type':'application/json','Accept':'application/json'}
    if key: headers['Authorization']='Bearer '+key
    failure=None; start=time.perf_counter()
    for attempt in range(retries+1):
        req=urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                obj=json.loads(resp.read().decode('utf-8'))
            return obj,(time.perf_counter()-start)*1000,None
        except urllib.error.HTTPError as exc:
            detail=exc.read(500).decode('utf-8','replace')
            failure=f'HTTP {exc.code}: {detail}'
            if exc.code not in (408,429,500,502,503,504): break
            retry_after=exc.headers.get('Retry-After')
            try: pause=min(float(retry_after),10) if retry_after else 0.5*(2**attempt)
            except (TypeError,ValueError): pause=0.5*(2**attempt)
        except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:
            failure=f'{type(exc).__name__}: {exc}'
            pause=0.5*(2**attempt)
        if attempt<retries: time.sleep(min(pause,12))
    return None,(time.perf_counter()-start)*1000,failure


def extract_probs(raw,keys):
    if not isinstance(raw,(dict,list)): return None
    output={}
    if isinstance(raw,list):
        for ix,k in enumerate(keys):
            if ix<len(raw): output[str(k)]=raw[ix]
    else:
        for ix,k in enumerate(keys):
            # Score response uses indices in the standard wire format; accept descriptions, too.
            options=(str(k),str(ix))
            for sk in options:
                if sk in raw:
                    output[str(k)]=raw[sk];break
    try:
        output={k:float(v) for k,v in output.items()}
        if any(not math.isfinite(v) or v<0 for v in output.values()): return None
        if len(output)!=len(keys): return None
        total=sum(output.values())
        if total<=0: return None
        if not 0.90<=total<=1.10: return None  # avoid treating logits as probabilities
        return {str(k):v/total for k,v in output.items()}
    except (TypeError,ValueError): return None


def parse_result(row, obj):
    answers=obj.get('answers',{})
    answer=answers.get(QID) if isinstance(answers,dict) else None
    if answer is None and isinstance(answers,dict) and len(answers)==1: answer=next(iter(answers.values()))
    if not isinstance(answer,dict): raise ValueError('Response missing answers.decision')
    kind=row['question']['type']
    criteria=row['question'].get('criteria')
    prob=None; conf=None
    if kind=='noul':
        p=float(answer.get('noul'))
        if not 0.0<=p<=1.0 or not math.isfinite(p): raise ValueError('Invalid noul probability')
        predicted=int(p>=0.5)
        conf=max(p,1-p)
        prob={'0':1-p,'1':p}
    elif kind=='choice':
        keys=list(criteria.keys())
        prob=extract_probs(answer.get('probabilities'),keys)
        predicted=answer.get('choice')
        if predicted not in keys:
            if prob: predicted=max(prob,key=prob.get)
            else: raise ValueError('Invalid choice and no valid probabilities')
        if prob: conf=prob[str(predicted)]
        else:
            raw=answer.get('confidence')
            conf=float(raw) if raw is not None and 0<=float(raw)<=1 else None
    elif kind=='score':
        keys=[str(x) for x in range(len(criteria))]
        prob=extract_probs(answer.get('probabilities'),keys)
        if prob:
            predicted=int(max(prob,key=prob.get))
            conf=prob[str(predicted)]
        else:
            number=answer.get('score')
            if number is None: raise ValueError('Invalid score and no probabilities')
            predicted=max(0,min(len(criteria)-1,int(round(float(number)))))
            # Do not treat unknown score confidence as a calibrated class probability.
            conf=None
    else: raise ValueError('Unknown question type')
    usage=obj.get('usage') if isinstance(obj.get('usage'),dict) else {}
    return {'prediction':predicted,'correct':int(str(predicted)==str(row['gold'])),
            'confidence':conf,'probabilities':prob,'input_tokens':usage.get('input_tokens'),
            'output_tokens':usage.get('output_tokens'),
            'server_latency_ms':obj.get('latency_ms')}


def as_result(item, run_index, args, key):
    payload,ms,error=call_api(item,args.endpoint,args.model,key,args.timeout,args.retries)
    record={k:item[k] for k in ('id','category','language')}
    record.update({'question_type':item['question']['type'],'gold':item['gold'],
                   'prediction':None,'correct':0,'confidence':None,'probabilities':None,
                   'latency_ms':round(ms,3),'server_latency_ms':None,'input_tokens':None,
                   'output_tokens':None,'run_index':run_index,'error':error})
    if payload is not None:
        try: record.update(parse_result(item,payload))
        except (TypeError,KeyError,ValueError,OverflowError) as exc: record['error']=f'PARSE: {exc}'
    return record


def summarize(rows):
    n=len(rows); success=[r for r in rows if not r.get('error')]; ok=len(success)
    acc=sum(r.get('correct',0) for r in rows)/n if n else None # all failures count as incorrect
    val={'count':n,'successful':ok,'errors':n-ok,'success_rate':ok/n if n else None,
         'accuracy_all':acc,'accuracy_success_only':sum(r.get('correct',0) for r in success)/ok if ok else None,
         'macro_f1':None,'ece_10bins':None,'brier_multiclass_or_binary':None,
         'latency_p50_ms':None,'latency_p95_ms':None,'latency_p99_ms':None,
         'latency_mean_ms':None,'score_mae':None,'score_within_one':None,'mean_confidence':None}
    if not n: return val
    observed=sorted(float(r['latency_ms']) for r in success if r.get('latency_ms') is not None)
    if observed:
        val.update(latency_p50_ms=pct(observed,50),latency_p95_ms=pct(observed,95),
                   latency_p99_ms=pct(observed,99),latency_mean_ms=round(statistics.mean(observed),3))
    if not success: return val
    classes=sorted(set(str(r['gold']) for r in success)|set(str(r['prediction']) for r in success))
    f1s=[]
    for label in classes:
        tp=sum(str(r['gold'])==label and str(r['prediction'])==label for r in success)
        fp=sum(str(r['gold'])!=label and str(r['prediction'])==label for r in success)
        fn=sum(str(r['gold'])==label and str(r['prediction'])!=label for r in success)
        f1s.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.)
    val['macro_f1']=round(statistics.mean(f1s),6)
    scored=[r for r in success if r['question_type']=='score']
    if scored:
        val['score_mae']=round(statistics.mean(abs(int(r['prediction'])-int(r['gold'])) for r in scored),6)
        val['score_within_one']=round(sum(abs(int(r['prediction'])-int(r['gold']))<=1 for r in scored)/len(scored),6)
    calibrated=[r for r in success if r.get('confidence') is not None]
    if calibrated:
        bins=[[] for _ in range(10)]
        for r in calibrated:
            idx=min(9,int(float(r['confidence'])*10));bins[idx].append(r)
        val['ece_10bins']=round(sum(len(b)/len(calibrated)*abs(statistics.mean(float(r['confidence']) for r in b)-statistics.mean(r['correct'] for r in b)) for b in bins if b),6)
        val['mean_confidence']=round(statistics.mean(float(r['confidence']) for r in calibrated),6)
    briers=[]
    for r in success:
        prob=r.get('probabilities')
        if not isinstance(prob,dict): continue
        # Multiclass Brier = sum_k (p_k - y_k)^2, for binary report p(yes)-y squared.
        if r['question_type']=='noul':
            briers.append((float(prob['1'])-float(r['gold']))**2)
        else:
            briers.append(sum((float(p)-(str(label)==str(r['gold'])))**2 for label,p in prob.items()))
    if briers: val['brier_multiclass_or_binary']=round(statistics.mean(briers),6)
    for threshold in (.7,.8,.9,.95):
        eligible=[r for r in success if r.get('confidence') is not None and float(r['confidence'])>=threshold]
        metric=f'gate_{threshold:.2f}'
        val[metric]={'coverage':round(len(eligible)/n,6),'accepted':len(eligible),
                     'error_rate':round(1-sum(r['correct'] for r in eligible)/len(eligible),6) if eligible else None,
                     'errors':sum(not r['correct'] for r in eligible)}
    return val


def pct(values,p):
    if not values: return None
    idx=(len(values)-1)*p/100
    lo,hi=math.floor(idx),math.ceil(idx)
    return round(values[lo]+(values[hi]-values[lo])*(idx-lo),3)


def group_summaries(rows):
    result={}
    for field in ('category','question_type','language'):
        grouped=collections.defaultdict(list)
        for row in rows: grouped[str(row.get(field,'unknown'))].append(row)
        result[field]={key:summarize(values) for key,values in sorted(grouped.items())}
    grouped=collections.defaultdict(list)
    # Option count cannot be inferred solely from results if no source label;
    # include it in result record for new runs and group if available.
    for row in rows:
        if 'num_options' in row: grouped[str(row['num_options'])].append(row)
    if grouped: result['num_options']={k:summarize(v) for k,v in sorted(grouped.items(),key=lambda it:int(it[0]))}
    return result


def compact(x):
    if x is None: return 'N/A'
    if isinstance(x,float): return f'{x:.3f}'
    return str(x)


def build_model_report(name,rows,output):
    output.mkdir(parents=True,exist_ok=True)
    primary=[r for r in rows if r.get('run_index',1)==1]
    primary_stats=summarize(primary)
    by_group=group_summaries(primary)
    # For repeated passes, verify same id returns identical decisions.
    repeat=collections.defaultdict(list)
    for r in rows:
        if not r.get('error'): repeat[r['id']].append(r['prediction'])
    multiple=[v for v in repeat.values() if len(v)>=2]
    repeat_consistency=sum(len(set(map(str,v)))==1 for v in multiple)/len(multiple) if multiple else None
    by_pass={str(j):summarize([r for r in rows if r.get('run_index',1)==j])
             for j in sorted({int(r.get('run_index',1)) for r in rows})}
    permutation=collections.defaultdict(list)
    for r in primary:
        if r.get('permutation_group') and not r.get('error'):
            permutation[r['permutation_group']].append(str(r['prediction']))
    variants=[x for x in permutation.values() if len(x)>=2]
    permutation_consistency=sum(len(set(x))==1 for x in variants)/len(variants) if variants else None
    p50_1=by_pass.get('1',{}).get('latency_p50_ms')
    p50_2=by_pass.get('2',{}).get('latency_p50_ms')
    report={'name':name,'created':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'primary':primary_stats,'all_runs':summarize(rows),'groups':by_group,'by_pass':by_pass,
            'repeat_consistency':repeat_consistency,'repeat_items':len(multiple),
            'option_order_consistency':permutation_consistency,'option_order_pairs':len(variants),
            'second_pass_vs_first_pass_p50_speedup':round(p50_1/p50_2,3) if p50_1 and p50_2 else None,
            'note':'Score exact class = argmax over ordinal-level probabilities. ECE uses predicted-label top probability. Failed requests count wrong in accuracy_all. Latency includes client/network time and retries.'}
    (output/f'{name}_metrics.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    with (output/f'{name}_metrics_by_category.csv').open('w',newline='',encoding='utf-8-sig') as f:
        cols=['category','count','successful','accuracy_all','macro_f1','ece_10bins','brier_multiclass_or_binary','latency_p50_ms','latency_p95_ms','latency_p99_ms','score_mae','score_within_one','gate_0.90_coverage','gate_0.90_error_rate']
        wr=csv.DictWriter(f,fieldnames=cols);wr.writeheader()
        for cat,val in by_group['category'].items():
            wr.writerow(dict(category=cat,count=val['count'],successful=val['successful'],accuracy_all=val['accuracy_all'],macro_f1=val['macro_f1'],ece_10bins=val['ece_10bins'],brier_multiclass_or_binary=val['brier_multiclass_or_binary'],latency_p50_ms=val['latency_p50_ms'],latency_p95_ms=val['latency_p95_ms'],latency_p99_ms=val['latency_p99_ms'],score_mae=val['score_mae'],score_within_one=val['score_within_one'],**{'gate_0.90_coverage':val['gate_0.90']['coverage'] if 'gate_0.90' in val else None,'gate_0.90_error_rate':val['gate_0.90']['error_rate'] if 'gate_0.90' in val else None}))
    return report


def safe_name(s):
    if not s or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in s):
        raise ValueError('Name must contain only ASCII letters, digits, underscores')
    return s


def sample_gpu(gpu_ids):
    try:
        cmd=['nvidia-smi','--query-gpu=index,memory.used,utilization.gpu','--format=csv,noheader,nounits']
        res=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
        if res.returncode: return []
        result=[]
        for line in res.stdout.strip().splitlines():
            fields=[x.strip() for x in line.split(',')]
            if len(fields)!=3: continue
            try:
                gid,mem,util=map(int,fields)
                if not gpu_ids or gid in gpu_ids: result.append({'gpu_id':gid,'memory_used_mb':mem,'gpu_util_pct':util})
            except ValueError: continue
        return result
    except (OSError,subprocess.TimeoutExpired): return []


def gpu_monitor(stop,results,ids,interval):
    while not stop.is_set():
        sample=sample_gpu(ids)
        if sample: results.append({'time':time.time(),'gpus':sample})
        stop.wait(interval)


def do_run(args):
    args.name=safe_name(args.name)
    args.endpoint=endpoint_url(args.endpoint)
    data,types=validate_dataset(args.dataset)
    if args.limit is not None: data=data[:args.limit]
    if args.shuffle: random.Random(args.seed).shuffle(data)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    result_path=out/f'{args.name}_predictions.jsonl'
    sig=hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest()
    run_meta=out/f'{args.name}_run_config.json'
    config={'name':args.name,'model':args.model,'endpoint':args.endpoint,'dataset_sha256':sig,
            'dataset':str(Path(args.dataset).resolve()),'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if run_meta.exists():
        old=json.loads(run_meta.read_text(encoding='utf-8'))
        for field in ('model','endpoint','dataset_sha256'):
            if old.get(field)!=config[field]: raise RuntimeError(f'{args.name}: old run {field} mismatch; use new --name or --out')
    else: run_meta.write_text(json.dumps(config,indent=2),encoding='utf-8')
    key=os.environ.get(args.api_key_env,'') if args.api_key_env else ''
    if args.require_key and not key: raise RuntimeError(f'Missing environment variable {args.api_key_env}')
    existing=read_jsonl(result_path) if result_path.exists() else []
    completed={(r['id'],int(r.get('run_index',1))) for r in existing}
    todos=[(item,j) for j in range(1,args.passes+1) for item in data if (item['id'],j) not in completed]
    print(f'Target={args.name}/{args.model} endpoint={args.endpoint}',flush=True)
    print(f'Dataset={len(data)} cases; kind={dict(types)}; already finished={len(completed)}; remaining={len(todos)}',flush=True)
    gpu_ids=[int(i) for i in args.gpus.split(',')] if args.gpus else []
    monitor_samples=[];monitor_stop=threading.Event();monitor_thread=None
    if args.gpus:
        monitor_thread=threading.Thread(target=gpu_monitor,args=(monitor_stop,monitor_samples,gpu_ids,.7),daemon=True)
        monitor_thread.start()
    start=None
    new_results=[]
    try:
        # Warm-up is excluded from timed QPS and dataset scoring.
        if args.warmup and data:
            print(f'Warm-up: {args.warmup} calls (excluded from scored results)',flush=True)
            for item in data[:min(args.warmup,len(data))]:
                call_api(item,args.endpoint,args.model,key,args.timeout,args.retries)
        start=time.perf_counter()
        with result_path.open('a',encoding='utf-8') as f:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                # Important: run pass 2 only AFTER all pass 1 requests have finished.
                # This makes first/second-pass comparison meaningful even at workers>1.
                processed=0
                for pass_id in range(1,args.passes+1):
                    pass_items=[item for item in data if (item['id'],pass_id) not in completed]
                    if not pass_items: continue
                    tasks={pool.submit(as_result,item,pass_id,args,key):item for item in pass_items}
                    for task in concurrent.futures.as_completed(tasks):
                        record=task.result()
                        item=tasks[task]
                        record['num_options']=len(item['question'].get('criteria',[])) if item['question']['type']!='noul' else 2
                        record['permutation_group']=item.get('metadata',{}).get('permutation_group')
                        f.write(json.dumps(record,ensure_ascii=False)+'\n');f.flush()
                        new_results.append(record)
                        processed+=1
                        if processed%50==0 or processed==len(todos):
                            print(f'{processed}/{len(todos)} completed; errors={sum(bool(r["error"]) for r in new_results)}',flush=True)
    finally:
        monitor_stop.set()
        if monitor_thread: monitor_thread.join(timeout=3)
    elapsed=time.perf_counter()-(start or time.perf_counter())
    all_records=read_jsonl(result_path)
    report=build_model_report(args.name,all_records,out)
    gpu_report={'gpus':gpu_ids,'samples':len(monitor_samples),'source':'nvidia-smi device global metrics, not per-process attributed',
                'memory_peak_used_mb':{},'gpu_util_mean_pct':{}}
    for gid in gpu_ids:
        matches=[sample for row in monitor_samples for sample in row['gpus'] if sample['gpu_id']==gid]
        if matches:
            gpu_report['memory_peak_used_mb'][str(gid)]=max(x['memory_used_mb'] for x in matches)
            gpu_report['gpu_util_mean_pct'][str(gid)]=round(statistics.mean(x['gpu_util_pct'] for x in matches),2)
    (out/f'{args.name}_gpu.json').write_text(json.dumps(gpu_report,indent=2),encoding='utf-8')
    measured={'batch_wall_seconds':round(elapsed,3),'new_requests':len(todos),
              'qps_new_requests':round(len(todos)/elapsed,3) if elapsed else None,
              'workers':args.workers,'warmup':args.warmup,'passes':args.passes}
    (out/f'{args.name}_throughput.json').write_text(json.dumps(measured,indent=2),encoding='utf-8')
    print('Metrics: '+str(out/f'{args.name}_metrics.json'),flush=True)
    print('Accuracy_all='+compact(report['primary']['accuracy_all'])+'; P95(ms)='+compact(report['primary']['latency_p95_ms'])+
          '; new-QPS='+compact(measured['qps_new_requests'])+'; GPU='+str(gpu_report['memory_peak_used_mb']),flush=True)


def maybe_pct(x): return 'N/A' if x is None else f'{x*100:.2f}%'

def hval(x): return 'N/A' if x is None else f'{x:.2f}'

def compare_reports(args):
    out=Path(args.out)
    models=[]
    for name in args.models:
        name=safe_name(name)
        records=read_jsonl(out/f'{name}_predictions.jsonl')
        config=json.loads((out/f'{name}_run_config.json').read_text())
        groups=group_summaries([r for r in records if r.get('run_index',1)==1])
        main=summarize([r for r in records if r.get('run_index',1)==1])
        gpu=json.loads((out/f'{name}_gpu.json').read_text()) if (out/f'{name}_gpu.json').exists() else {}
        perf=json.loads((out/f'{name}_throughput.json').read_text()) if (out/f'{name}_throughput.json').exists() else {}
        models.append({'name':name,'main':main,'groups':groups,'gpu':gpu,'perf':perf,'config':config,'records':records})
    if len(set(m['config']['dataset_sha256'] for m in models))>1:
        raise ValueError('Cannot compare different dataset SHA256 values')
    # paired accuracy difference / disagreements only for ids shared by first pass
    byid=[]
    for model in models:
        byid.append({r['id']:r for r in model['records'] if r.get('run_index',1)==1})
    shared=set.intersection(*[set(d) for d in byid]) if byid else set()
    paired=[]
    if len(models)==2:
        d0,d1=byid
        for case_id in sorted(shared):
            a,b=d0[case_id],d1[case_id]
            paired.append({'id':case_id,'category':a['category'],'gold':a['gold'],
                           f'{models[0]["name"]}_prediction':a.get('prediction'),
                           f'{models[1]["name"]}_prediction':b.get('prediction'),
                           f'{models[0]["name"]}_correct':a.get('correct',0),
                           f'{models[1]["name"]}_correct':b.get('correct',0),
                           'winner':models[0]['name'] if a.get('correct',0)>b.get('correct',0) else (models[1]['name'] if b.get('correct',0)>a.get('correct',0) else 'tie')})
        with (out/'paired_case_differences.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(paired[0]) if paired else ['id']);w.writeheader();w.writerows(paired)
    rows=[]
    for model in models:
        m=model['main']; perf=model['perf']
        rows.append({'model':model['name'],'tested':m['count'],'success_rate':m['success_rate'],
                     'accuracy_all':m['accuracy_all'],'macro_f1':m['macro_f1'],'ece_10bins':m['ece_10bins'],
                     'brier':m['brier_multiclass_or_binary'],'latency_p50_ms':m['latency_p50_ms'],
                     'latency_p95_ms':m['latency_p95_ms'],'latency_p99_ms':m['latency_p99_ms'],
                     'score_mae':m['score_mae'],'gate_90_coverage':m.get('gate_0.90',{}).get('coverage'),
                     'gate_90_error_rate':m.get('gate_0.90',{}).get('error_rate'),
                     'qps':perf.get('qps_new_requests'),'memory_peak_used_mb':json.dumps(model['gpu'].get('memory_peak_used_mb',{}),ensure_ascii=False)})
    with (out/'comparison_summary.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    comparisons=[]
    if len(models)==2:
        a,b=models[0],models[1]
        for cat in sorted(set(a['groups']['category'])|set(b['groups']['category'])):
            x=a['groups']['category'].get(cat,{});y=b['groups']['category'].get(cat,{})
            ax=x.get('accuracy_all');by=y.get('accuracy_all')
            comparisons.append({'category':cat,'first_accuracy':ax,'second_accuracy':by,
                                'first_minus_second_points':round(100*(ax-by),3) if ax is not None and by is not None else None,
                                'first_p95_ms':x.get('latency_p95_ms'),'second_p95_ms':y.get('latency_p95_ms')})
        with (out/'comparison_by_category.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(comparisons[0]));w.writeheader();w.writerows(comparisons)
    doc=['# CLM / Jev Decision Benchmark Report','',
         'This report contains **measured model outputs only**. The bundled 1,300 labels are synthetic and have not undergone expert validation.','',
         '| Model | n | Success | Accuracy(all) | Macro-F1 | ECE | P50 (ms) | P95 (ms) | P99 (ms) | QPS |',
         '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        doc.append(f'| {r["model"]} | {r["tested"]} | {maybe_pct(r["success_rate"])} | {maybe_pct(r["accuracy_all"])} | {hval(r["macro_f1"])} | {hval(r["ece_10bins"])} | {hval(r["latency_p50_ms"])} | {hval(r["latency_p95_ms"])} | {hval(r["latency_p99_ms"])} | {hval(r["qps"])} |')
    doc+=['','## Categories','', '| Category | '+ ' | '.join(m['name']+' accuracy' for m in models)+' |', '|---|'+'---:|'*len(models)]
    cats=sorted(set().union(*(m['groups']['category'] for m in models)))
    for category in cats:
        doc.append('| '+category+' | '+' | '.join(maybe_pct(m['groups']['category'].get(category,{}).get('accuracy_all')) for m in models)+' |')
    doc+=['','## Calibration, inference and fairness notes','',
          '- Accuracy(all) counts failed or invalid calls as incorrect; success-only accuracy is in per-model JSON.',
          '- ECE uses predicted-label probability from model distributions; a smaller value is better. Results are sample-dependent.',
          '- Binary Brier uses (P(yes)-y)^2; multi-class Brier is sum over classes and is not directly comparable to binary values.',
          '- Score exact match comes from argmax probability over ordinal levels (or rounded expected score if probabilities unavailable); MAE is provided.',
          '- 0.90 gate coverage = all evaluated cases automatically accepted / all evaluated cases; gate error rate = errors / accepted.',
          '- P50/P95/P99 are observed CLIENT wall times per request, including network and retries, not isolated GPU kernel time.',
          '- QPS comes from the LAST executed request batch; it depends on worker count and should be compared only under matched loads.',
          '- GPU memory is nvidia-smi global device used memory, NOT per-model attributable memory when GPUs share workloads.',
          '- The option-permutation category contains paired variants and is not statistically independent from its base set.',
          '- Synthetic tasks are for smoke/regression testing. Prefer public benchmark and separately human-verified samples for performance claims.',
          '']
    if paired:
        doc.append(f'Paired shared samples: **{len(paired)}**. Both-correct={sum(d[f"{models[0]["name"]}_correct"] and d[f"{models[1]["name"]}_correct"] for d in paired)}. Each-model-only wins are in `paired_case_differences.csv`.')
    (out/'comparison_report.md').write_text('\n'.join(doc),encoding='utf-8')
    html_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k in ['model','tested','success_rate','accuracy_all','macro_f1','ece_10bins','latency_p50_ms','latency_p95_ms','latency_p99_ms','qps'])+'</tr>' for r in rows)
    cat_rows=''.join('<tr><td>'+html.escape(c)+'</td>'+''.join(f'<td>{html.escape(maybe_pct(m["groups"]["category"].get(c,{}).get("accuracy_all")))}</td>' for m in models)+'</tr>' for c in cats)
    heads=['Model','N','Success','Accuracy','Macro-F1','ECE','P50 ms','P95 ms','P99 ms','QPS']
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>CLM / Jev 评测报告</title>
<style>body{font:15px -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f5f7fb;color:#1e293b;max-width:1200px;margin:36px auto;padding:0 22px}h1{font-size:28px}.hint{color:#475569;background:#e9edf5;padding:15px;border-radius:12px}.panel{background:white;border:1px solid #dfe6ef;border-radius:14px;padding:22px;margin-top:20px;overflow-x:auto}table{border-collapse:collapse;width:100%;margin-top:12px}th{text-align:left;background:#172554;color:white;padding:12px;white-space:nowrap}td{padding:12px;border-bottom:1px solid #e2e8f0;white-space:nowrap}tr:hover{background:#f8fafc}small{color:#64748b}</style><h1>CLM / Jev 决策模型评测报告</h1><div class="hint"><b>方法说明：</b>统计来自真实 API 返回记录；随包合成标签未经专家复核，不是公开权威基准。延迟包含网络与重试；GPU 显存是设备全局值。</div><section class="panel"><h2>模型对比</h2><table><thead><tr>'''+''.join('<th>'+x+'</th>' for x in heads)+'</tr></thead><tbody>'+html_rows+'</tbody></table></section><section class="panel"><h2>分类场景正确率</h2><table><thead><tr><th>场景</th>'+''.join('<th>'+html.escape(m['name'])+'</th>' for m in models)+'</tr></thead><tbody>'+cat_rows+'</tbody></table></section><p><small>完整指标及逐条预测请查阅 JSON/CSV 和 Markdown 报告。ECE 越低越好；Accuracy/F1 越高越好。</small></p></html>'
    (out/'comparison_report.html').write_text(page,encoding='utf-8')
    summary={'models':rows,'dataset_sha256':models[0]['config']['dataset_sha256'],'paired_count':len(paired),'by_category':comparisons}
    (out/'comparison_report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Comparison saved:',out/'comparison_report.html')
    for r in rows: print(r['model'],'accuracy',maybe_pct(r['accuracy_all']),'p95',hval(r['latency_p95_ms']))


def main():
    p=argparse.ArgumentParser(description='CLM / Jev 1,300-decision benchmark (stdlib only)')
    subs=p.add_subparsers(dest='command',required=True)
    v=subs.add_parser('validate',help='Validate dataset and show label counts')
    v.add_argument('--dataset',default='data/clm_jev_1300.jsonl')
    r=subs.add_parser('run',help='Run one model; supports resume and multi-pass')
    r.add_argument('--dataset',default='data/clm_jev_1300.jsonl')
    r.add_argument('--endpoint',default='http://127.0.0.1:8700')
    r.add_argument('--model',default='clm-latest')
    r.add_argument('--name',required=True,help='ASCII output filename prefix, e.g. clm_latest')
    r.add_argument('--out',default='results')
    r.add_argument('--api-key-env',default='CLM_API_KEY')
    r.add_argument('--require-key',action='store_true')
    r.add_argument('--timeout',type=float,default=120)
    r.add_argument('--retries',type=int,default=2)
    r.add_argument('--workers',type=int,default=1)
    r.add_argument('--passes',type=int,default=1)
    r.add_argument('--warmup',type=int,default=5)
    r.add_argument('--gpus',default='',help='Physical nvidia-smi GPU IDs, e.g. 3,4')
    r.add_argument('--limit',type=int)
    r.add_argument('--shuffle',action='store_true')
    r.add_argument('--seed',type=int,default=20261013)
    c=subs.add_parser('compare',help='Build comparable CSV, JSON, Markdown and HTML')
    c.add_argument('--out',default='results')
    c.add_argument('--models',nargs='+',required=True)
    args=p.parse_args()
    if args.command=='validate':
        data,counts=validate_dataset(args.dataset)
        print(f'VALID: {len(data)} items | types {dict(counts)} | languages {dict(collections.Counter(x["language"] for x in data))}')
        print('categories:',dict(collections.Counter(x['category'] for x in data)))
    if args.command=='run':
        if args.workers<1 or args.passes<1: p.error('--workers and --passes must be >= 1')
        do_run(args)
    if args.command=='compare':compare_reports(args)

if __name__=='__main__':main()
