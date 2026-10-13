#!/usr/bin/env python3
"""LOCAL MOCK for transport QA ONLY. Never use fake predictions as actual CLM/Jev evidence."""
import argparse, json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/health','/v1/models']:
            obj={'ok':True} if self.path=='/health' else {'data':[{'id':'mock-test-model'}]}
            return self.respond(obj)
        self.send_error(404)
    def do_POST(self):
        if self.path!='/v1/systemone':return self.send_error(404)
        length=int(self.headers.get('Content-Length','0'))
        try:
            inp=json.loads(self.rfile.read(length).decode('utf-8'))
            answers={}
            for key,q in inp['questions'].items():
                kind=q['type']
                if kind=='noul': answers[key]={'noul':0.8}
                elif kind=='choice':
                    keys=list(q['criteria'])
                    prob={k:(0.9 if i==0 else 0.1/(len(keys)-1)) for i,k in enumerate(keys)}
                    answers[key]={'choice':keys[0],'probabilities':prob,'confidence':0.9}
                elif kind=='score':
                    levels=len(q['criteria']); pr=[.05/(levels-1) for _ in range(levels)];pr[2 if levels>2 else 0]=.95
                    answers[key]={'score':sum(i*p for i,p in enumerate(pr)),'probabilities':{str(i):v for i,v in enumerate(pr)},'confidence':.95}
                else: raise ValueError('bad type')
            self.respond({'model':inp.get('model'),'answers':answers,'usage':{'input_tokens':55,'output_tokens':0},'latency_ms':3.14})
        except Exception as exc: self.respond({'error':str(exc)},400)
    def respond(self,obj,status=200):
        body=json.dumps(obj).encode('utf-8');self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=18700);args=p.parse_args()
    print('MOCK ONLY port',args.port,flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
