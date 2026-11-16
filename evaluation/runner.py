#!/usr/bin/env python3
"""Offline scenario runner and blind comparison builder for Vincent."""
import argparse, datetime as dt, hashlib, json, os, random, sys, urllib.error, urllib.request
from pathlib import Path

HERE=Path(__file__).resolve().parent

def read_json(path):
    with Path(path).open(encoding='utf8') as f: return json.load(f)

def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def load_scenarios(path):
    data=read_json(path)
    if not isinstance(data,list) or not data: raise ValueError('Scenarios must be a nonempty array')
    ids=set()
    for case in data:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or not case['id'] or case['id'] in ids: raise ValueError('Scenario IDs must be unique nonempty strings')
        ids.add(case['id'])
        if case.get('mode') not in ('direct','observe') or not isinstance(case.get('context'),list) or not case['context']: raise ValueError('Invalid scenario mode or context')
        for message in case['context']:
            if not isinstance(message,dict) or not isinstance(message.get('author'),str) or not isinstance(message.get('text'),str): raise ValueError('Invalid context message')
    return data

def model_input(case):
    # Explicit allowlist: rubric, expected output, and future follow-ups are never sent.
    messages=[]
    for row in case['context']:
        msg={'author':row['author'],'text':row['text']}
        if 'reply_to_author' in row: msg['reply_to_author']=row['reply_to_author']
        messages.append(msg)
    return {'mode':case['mode'].upper(),'recent_messages':messages}

def generate_openrouter(model,system,case,temperature,max_tokens):
    key=os.environ.get('OPENROUTER_API_KEY')
    if not key: raise ValueError('Set OPENROUTER_API_KEY in the environment')
    body={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(model_input(case),ensure_ascii=False)}], 'temperature':temperature,'max_tokens':max_tokens}
    req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=60) as response: data=json.load(response)
        content=data['choices'][0]['message']['content']
        if not isinstance(content,str) or not content.strip(): raise ValueError('Provider returned an empty response')
        return content.strip()
    except (urllib.error.URLError,KeyError,IndexError,TypeError,json.JSONDecodeError) as exc:
        raise RuntimeError('Provider request failed: '+type(exc).__name__) from exc

def run(args):
    scenarios=load_scenarios(args.scenarios); prompt=Path(args.prompt).read_text(encoding='utf8'); fixtures=read_json(args.fixtures) if args.backend=='fixture' else None
    if args.backend=='openrouter' and not args.model: raise ValueError('--model is required with openrouter')
    if fixtures is not None and (not isinstance(fixtures,dict) or any(not isinstance(fixtures.get(c['id']),str) or not fixtures[c['id']].strip() for c in scenarios)): raise ValueError('Fixture must contain one nonempty response per case')
    results=[]
    for case in scenarios:
        result=fixtures[case['id']] if fixtures is not None else generate_openrouter(args.model,prompt,case,args.temperature,args.max_tokens)
        results.append({'scenario_id':case['id'],'response':result})
    out={'format_version':1,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'backend':args.backend,'model':args.model if args.backend=='openrouter' else 'fixture','scenario_sha256':digest(args.scenarios),'prompt_sha256':digest(args.prompt),'fixture_sha256':digest(args.fixtures) if fixtures is not None else None,'settings':{'temperature':args.temperature,'max_tokens':args.max_tokens} if args.backend=='openrouter' else {},'results':results}
    with Path(args.output).open('x',encoding='utf8') as f: json.dump(out,f,indent=2,ensure_ascii=False);f.write('\n')
    print('Wrote',len(results),'results to',args.output)

def compare(args):
    a,b=read_json(args.run_a),read_json(args.run_b); cases=load_scenarios(args.scenarios)
    if a.get('scenario_sha256')!=digest(args.scenarios) or b.get('scenario_sha256')!=digest(args.scenarios):raise ValueError('Runs do not match the supplied scenario file')
    def result_map(run):
        rows=run.get('results',[]); mapping={r['scenario_id']:r['response'] for r in rows}
        if len(mapping)!=len(rows) or set(mapping)!={c['id'] for c in cases}:raise ValueError('Runs must contain each scenario exactly once')
        return mapping
    aa,bb=result_map(a),result_map(b); rng=random.Random(args.seed); order=list(range(len(cases)));rng.shuffle(order)
    swaps=[False]*(len(cases)//2)+[True]*(len(cases)-len(cases)//2);rng.shuffle(swaps)
    rows=[];mapping={}
    for n,i in enumerate(order):
        c=cases[i]; swap=swaps[n]; case_id='case-'+str(n+1).zfill(3); responses=[aa[c['id']],bb[c['id']]]
        if swap:responses.reverse()
        rows.append({'case':case_id,'mode':c['mode'],'context':model_input(c)['recent_messages'],'response_A':responses[0],'response_B':responses[1],'review_focus':c.get('review_focus',''),'rating':{'preferred':None,'A':{'respond_appropriate':None,'understanding':None,'voice':None,'timing':None,'creativity':None,'entertainment':None,'wildness':None,'flags':[]},'B':{'respond_appropriate':None,'understanding':None,'voice':None,'timing':None,'creativity':None,'entertainment':None,'wildness':None,'flags':[]},'notes':None}})
        mapping[case_id]={'scenario_id':c['id'],'A':'run_b' if swap else 'run_a','B':'run_a' if swap else 'run_b'}
    # Don't overwrite one file while failing to create the other.
    if Path(args.output).exists() or Path(args.key).exists() or Path(args.output).resolve()==Path(args.key).resolve():raise ValueError('Output and key must be distinct new files')
    with Path(args.output).open('x',encoding='utf8') as f: json.dump(rows,f,indent=2,ensure_ascii=False);f.write('\n')
    with Path(args.key).open('x',encoding='utf8') as f:json.dump({'run_a':args.run_a,'run_b':args.run_b,'seed':args.seed,'mapping':mapping},f,indent=2);f.write('\n')
    print('Wrote',len(rows),'blind cases to',args.output,'and separate key to',args.key)

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('run');r.add_argument('--scenarios',default=str(HERE/'scenarios.json'));r.add_argument('--prompt',default=str(HERE/'persona.txt'));r.add_argument('--backend',choices=['fixture','openrouter'],required=True);r.add_argument('--fixtures',default=str(HERE/'fixtures.json'));r.add_argument('--model');r.add_argument('--temperature',type=float,default=1.0);r.add_argument('--max-tokens',type=int,default=400);r.add_argument('--output',required=True);r.set_defaults(func=run)
    c=sub.add_parser('compare');c.add_argument('--scenarios',default=str(HERE/'scenarios.json'));c.add_argument('--run-a',required=True);c.add_argument('--run-b',required=True);c.add_argument('--output',required=True);c.add_argument('--key',required=True);c.add_argument('--seed',type=int,default=11);c.set_defaults(func=compare)
    args=p.parse_args()
    try:args.func(args)
    except (ValueError,RuntimeError,OSError,json.JSONDecodeError) as exc:print('Error:',exc,file=sys.stderr);return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
