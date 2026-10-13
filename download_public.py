#!/usr/bin/env python3
"""Download publicly accessible, labeled benchmark samples DIRECTLY from Hugging Face.
Requires outbound HTTPS. Respect the dataset-specific licence; nothing is bundled here.
Use a deterministic fixed seed; disjoint source splits are not redefined.
"""
import argparse, json, math, random, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

SOURCES={
'boolq':{'dataset':'google/boolq','config':'default','split':'validation','license':'CC BY-SA 3.0',
         'card':'https://huggingface.co/datasets/google/boolq'},
'ag_news':{'dataset':'fancyzhx/ag_news','config':'default','split':'test','license':'Unknown (review dataset card before redistributing)',
           'card':'https://huggingface.co/datasets/fancyzhx/ag_news'}
}


def fetch(params,retries=3):
    url='https://datasets-server.huggingface.co/rows?'+urllib.parse.urlencode(params)
    exc_last=None
    for i in range(retries+1):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'CLM-JEV-evaluator/1.0','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=35) as r: return json.load(r)
        except (urllib.error.URLError,TimeoutError,ValueError) as e:
            exc_last=e
            if i<retries:time.sleep(min(1.25*(2**i),10))
    raise RuntimeError(f'Could not fetch Hugging Face dataset. URL: {url} error={exc_last}')


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',choices=sorted(SOURCES),required=True)
    p.add_argument('--count',type=int,default=600);p.add_argument('--seed',type=int,default=20261013)
    p.add_argument('--out',default=None)
    args=p.parse_args();src=SOURCES[args.source]
    first=fetch(dict(dataset=src['dataset'],config=src['config'],split=src['split'],offset=0,length=100))
    total=int(first['num_rows_total'])
    if args.count>total:p.error(f'count cannot exceed {total}')
    # Request a reproducible random set of pages across the entire split.
    pages=math.ceil(total/100);order=list(range(pages))
    random.Random(args.seed).shuffle(order)
    sample=[];seen=set()
    for index,page_id in enumerate(order):
        result=first if page_id==0 else fetch(dict(dataset=src['dataset'],config=src['config'],split=src['split'],offset=page_id*100,length=min(100,total-page_id*100)))
        if result.get('partial') is True: raise RuntimeError('Dataset-server returned partial content; abort for data integrity')
        for item in result.get('rows',[]):
            if item.get('truncated_cells'):continue
            row=item['row'];row_id=int(item['row_idx'])
            if row_id in seen:continue
            seen.add(row_id)
            if args.source=='boolq':
                passage=row.get('passage','');question=row.get('question','')
                # Skip exceptionally long examples; no truncation so the gold remains valid.
                if not passage or not question or len(passage)>6000: continue
                state=passage
                prompt=f'According to the preceding passage, is the answer to this question yes? Question: {question}'
                kind='noul';gold=int(bool(row['answer']));q={'type':kind,'instructions':prompt}
            else:
                label=int(row['label'])
                if label not in [0,1,2,3]:continue
                state=str(row['text'])
                if not state:continue
                kind='choice';gold=['world','sports','business','science_technology'][label]
                q={'type':'choice','instructions':'Choose the primary news desk for this article.',
                   'criteria':{'world':'World affairs and geopolitics','sports':'Sports and competitions',
                               'business':'Business and economics','science_technology':'Science and technology'}}
            sample.append({'id':f'PUB-{args.source.upper()}-{row_id:06d}',
                           'category':'public_'+args.source,'language':'en','state':state,
                           'question':q,'gold':gold,'gold_source':'public_original_label',
                           'metadata':{'dataset':src['dataset'],'split':src['split'],'source_row_idx':row_id,
                                       'license':src['license'],'source_url':src['card']}})
        if len(sample)>=args.count:break
        print(f'Fetched {index+1}/{len(order)} pages; accepted rows={len(sample)}',flush=True)
        time.sleep(.15)
    if len(sample)<args.count:raise RuntimeError(f'Only found {len(sample)} usable samples; requested {args.count}')
    random.Random(args.seed).shuffle(sample)
    sample=sample[:args.count]
    out=Path(args.out or f'data/public_{args.source}_{args.count}.jsonl')
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf-8') as fp:
        for r in sample:fp.write(json.dumps(r,ensure_ascii=False)+'\n')
    print('Saved',out,'cases',len(sample),'source=',src['card'],'license=',src['license'])
    print('NOTE: Data licensing, training-data overlap, data reliability and outbound network access remain user responsibilities.')
if __name__=='__main__':main()
