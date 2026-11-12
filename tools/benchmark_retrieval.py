"""Synthetic engine stress benchmark; not a test of the application's 200-item space limit."""
import argparse
import json
import math
from pathlib import Path
import statistics
import time
import urllib.request

from yiban.memory.retrieval import MemoryRetriever, DEFAULT_CONFIG
from yiban.settings import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',type=int,default=1000)
    parser.add_argument('--queries',type=int,default=200)
    args=parser.parse_args()
    if not 1<=args.records<=5000 or not 1<=args.queries<=1000:parser.error('Invalid benchmark size')
    directory=ROOT/'.runtime/retrieval-benchmark';directory.mkdir(parents=True,exist_ok=True)
    cache=directory/('vectors-'+str(time.time_ns())+'.sqlite3')
    items=[{'id':str(i),'version':1,'status':'valid','content':f'第{i+1}期读书交流会在青松图书馆举行，李老师负责带队，第{i%28+1}日下午三点集合。'} for i in range(args.records)]
    retriever=MemoryRetriever(dict(DEFAULT_CONFIG,timeout=120,batch_size=32),cache)
    cold=retriever.retrieve('读书交流在哪里集合？',items,namespace='benchmark')
    if cold['fallback']:raise RuntimeError('Cold index failed; do not report fallback as semantic performance')
    rows=[]
    for i in range(args.queries):
        result=retriever.retrieve(f'第{i%args.records+1}期读书活动谁带队？',items,namespace='benchmark')
        rows.append(result)
    durations=sorted(r['elapsed_ms'] for r in rows)
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(DEFAULT_CONFIG['base_url']+'/api/ps',timeout=5) as response:models=json.load(response)
        allocation=[{key:m.get(key) for key in ('name','size','size_vram')} for m in models.get('models',[]) if m.get('name')==DEFAULT_CONFIG['model']]
    except Exception:allocation=[]
    report={'records':args.records,'warm_queries':args.queries,'model':DEFAULT_CONFIG['model'],
            'cold_index_ms':cold['elapsed_ms'],'warm_median_ms':statistics.median(durations),
            'warm_p95_ms':durations[math.ceil(.95*len(durations))-1],
            'fallback_queries':sum(r['fallback'] for r in rows),'all_warm_cache_hits':all(r['cache_hits']==args.records for r in rows),
            'index_bytes':cache.stat().st_size,'ollama_reported_allocation':allocation,
            'limitations':'Synthetic engine benchmark, not live HTTP/LLM latency or application capacity. Cold means empty vector index, not necessarily unloaded model. Ollama allocation is not process RSS. Test device details are withheld; timings are not a cross-device performance guarantee.'}
    path=directory/'report.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
