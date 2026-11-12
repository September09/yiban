"""Reproducible retrieval comparison using synthetic data only; never opens user memory DB."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from yiban.memory.retrieval import MemoryRetriever, DEFAULT_CONFIG, PREPROCESS_VERSION, OllamaEmbedding
from yiban.settings import ROOT


def candidates_for(dataset, case):
    if case['actor']=='outsider':return []
    return [dict(x) for x in dataset['records'] if x['space']==case['space']
            and (x['visibility']=='all' or case['actor'] in x['viewers'])
            and (not case.get('public_only') or x['visibility']=='all')
            and (case['include_outdated'] or x['status']!='outdated')]


def legacy(query,candidates):
    import re
    def words(text):
        text=re.sub(r'\s+','',text.lower())
        return {text[i:i+2] for i in range(len(text)-1)}
    terms=words(query);priority={'valid':0,'pending':1,'outdated':2}
    return sorted(candidates,key=lambda x:(priority[x['status']],-len(terms&words(x['content']))))[:5]


def evaluate(dataset,split,strategy,config,cache):
    retriever=MemoryRetriever(dict(config,strategy=strategy if strategy!='legacy' else 'lexical'),cache)
    rows=[]
    for case in dataset['cases']:
        if case['split']!=split:continue
        candidates=candidates_for(dataset,case);start=time.monotonic()
        if strategy=='legacy':
            hits=legacy(case['query'],candidates)
            result={'hits':hits,'fallback':False,'strategy':'legacy','elapsed_ms':round((time.monotonic()-start)*1000)}
        else:result=retriever.retrieve(case['query'],candidates,namespace='eval:'+case['space'])
        ids=[x['id'] for x in result['hits']];expected=case['expected_ids']
        rows.append({'id':case['id'],'category':case['category'],'expected_ids':expected,'actual_ids':ids,
                     'hit':bool(set(ids)&set(expected)) if expected else None,'false_match':bool(ids) if not expected else None,
                     'recall':len(set(ids)&set(expected))/len(expected) if expected else None,
                     'unauthorized':len(set(ids)-{x['id'] for x in candidates}),
                     'fallback':result['fallback'],'elapsed_ms':result['elapsed_ms']})
    answerable=[x for x in rows if x['hit'] is not None]
    ordinary=[x for x in rows if x['false_match'] is not None and x['category'] not in ('selected_visibility','outsider')]
    security=[x for x in rows if x['category'] in ('selected_visibility','outsider')]
    metrics={'cases':len(rows),'answerable':len(answerable),'hit_at_5_count':sum(x['hit'] for x in answerable),
             'hit_at_5':round(sum(x['hit'] for x in answerable)/max(1,len(answerable)),4),
             'recall_at_5':round(statistics.mean(x['recall'] for x in answerable),4) if answerable else 0,
             'ordinary_no_answer':len(ordinary),'false_match_count':sum(x['false_match'] for x in ordinary),
             'false_match_rate':round(sum(x['false_match'] for x in ordinary)/max(1,len(ordinary)),4),
             'security_cases':len(security),'security_false_match_count':sum(x['false_match'] for x in security),
             'unauthorized_hits':sum(x['unauthorized'] for x in rows),'fallback_cases':sum(x['fallback'] for x in rows),
             'median_ms':statistics.median(x['elapsed_ms'] for x in rows),
             'p95_ms':sorted(x['elapsed_ms'] for x in rows)[max(0,int(len(rows)*.95)-1)]}
    categories={}
    for kind in sorted({x['category'] for x in rows}):
        group=[x for x in rows if x['category']==kind]
        categories[kind]={'count':len(group),'hits':sum(x['hit'] is True for x in group),'false_matches':sum(x['false_match'] is True for x in group)}
    return {'strategy':strategy,'metrics':metrics,'categories':categories,'cases':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split',choices=['dev','test'],default='dev')
    parser.add_argument('--strategy',choices=['legacy','lexical','hybrid','all'],default='all')
    parser.add_argument('--min-similarity',type=float,default=DEFAULT_CONFIG['min_similarity'])
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    path=ROOT/'tests/fixtures/retrieval/synthetic-v1.json';raw=path.read_bytes();dataset=json.loads(raw)
    strategies=['legacy','lexical','hybrid'] if args.strategy=='all' else [args.strategy]
    config=dict(DEFAULT_CONFIG,min_similarity=args.min_similarity)
    report={'dataset':dataset['version'],'dataset_sha256':hashlib.sha256(raw).hexdigest(),'split':args.split,'config':config,'results':[],
            'limitations':'Synthetic, developer-authored data. No human/real-data validation. Retrieval metrics are not final-answer quality.'}
    report['runtime']={'python':sys.version.split()[0],'preprocessing':PREPROCESS_VERSION,'embedding_context':4096}
    if 'hybrid' in strategies:
        from yiban.turns import Cancellation
        try:report['runtime']['model_fingerprint']=OllamaEmbedding(config).fingerprint(Cancellation(),time.monotonic()+5)
        except Exception:report['runtime']['model_fingerprint']=None
    for strategy in strategies:
        result=evaluate(dataset,args.split,strategy,config,ROOT/'.runtime/retrieval-eval/vectors.sqlite3')
        report['results'].append(result);print(json.dumps({'strategy':strategy,**result['metrics']},ensure_ascii=False),flush=True)
    output=args.output or ROOT/'.runtime'/('retrieval-'+args.split+'-'+args.strategy+'.json')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Report:',output)


if __name__=='__main__':main()
