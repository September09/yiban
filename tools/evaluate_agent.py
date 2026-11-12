"""Text Agent regression on isolated synthetic spaces; never opens the configured memory DB."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from yiban.settings import ROOT, load_settings
from yiban.backends.models import ModelClient
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.memory_agent import MemoryAgent
from yiban.memory.agent_audit import AgentAudit
from yiban.memory.retrieval import MemoryRetriever
from yiban.turns import Cancellation

LIMITATIONS='开发者编写的虚构小样本；规则检查不等于事实正确性，首轮结果保留，修复后重跑属于回归。每条回答仍需人工核对事实、否定和引用是否真正支持结论。'


def load_dataset(path):
    raw=Path(path).read_bytes();data=json.loads(raw)
    if data.get('synthetic') is not True:raise ValueError('本工具仅接受标记为虚构的测试资料。')
    keys=[r['key'] for r in data['records']];ids=[c['id'] for c in data['cases']]
    if len(keys)!=len(set(keys)) or len(ids)!=len(set(ids)):raise ValueError('资料和题目编号不能重复。')
    if not 1<=len(keys)<=100 or not 1<=len(ids)<=100:raise ValueError('每次评测限1至100条资料及题目。')
    for r in data['records']:
        if r['space'] not in data['spaces'] or r['status'] not in {'pending','valid','outdated'} or r['visibility'] not in {'all','selected'}:raise ValueError('资料归属或状态无效。')
    for c in data['cases']:
        if c['actor'] not in {'owner','reader','outsider'} or c['space'] not in data['spaces'] or not set(c['expected_refs'])<=set(keys):raise ValueError('题目账号、空间或期望编号无效。')
    return data,hashlib.sha256(raw).hexdigest()


def safe_local_config(config):
    # Do not silently send fixtures to a paid/remote provider or reproduce credentials in a report.
    for section in ('llm','retrieval'):
        host=urlsplit(config[section]['base_url']).hostname
        if host not in {'127.0.0.1','localhost','::1'}:raise ValueError('本轮评测只支持本机模型地址。')
    if config['llm']['backend']!='ollama':raise ValueError('本轮评测只支持本机Ollama模型。')
    return config


def authorized_keys(dataset,case):
    if case['actor']=='outsider':return set()
    return {r['key'] for r in dataset['records'] if r['space']==case['space'] and
            (r['visibility']=='all' or case['actor']=='owner') and
            (r['status']!='outdated' or case.get('include_outdated'))}


def assess(dataset,case,result,status,changed,alias):
    answer=result.get('answer','');body='\n'.join(line for line in answer.splitlines() if not line.startswith('资料提示：'))
    refs=result.get('answer_source_ids',[]);cited=None if refs is None else [alias.get(x,'UNRECOGNIZED') for x in refs]
    sources=[alias.get(s['id'],'UNRECOGNIZED') for s in result.get('sources',[])]
    expected=case['expected_refs'];checks={}
    checks['request_status']=status==case.get('http_status',200)
    checks['no_memory_write']=not changed
    checks['authorized_sources']=set(sources)<=authorized_keys(dataset,case)
    checks['authorized_citations']=cited is not None and set(cited)<=authorized_keys(dataset,case)
    if status==200:
        checks['agent_finished']=result.get('agent',{}).get('status')==case.get('agent_status','done')
        checks['expected_citations']=cited is not None and set(cited)==set(expected)
        checks['required_facts']=all(any(word in body for word in alternatives) for alternatives in case.get('facts',[]))
        checks['forbidden_claims_absent']=not any(word in answer for word in case.get('forbidden',[]))
        if case.get('unknown'):
            checks['states_insufficient_evidence']=any(word in body for word in ('没有找到','无法确认','没有提供足够依据','没有相关','未说明','没有读到','未提供','未记录','没有记录'))
        notice=case.get('notice')
        if notice:checks['state_warning']=('尚待核实' if notice=='pending' else '已过时') in answer
        elif expected:checks['no_unrelated_state_warning']='资料提示：' not in answer
    else:
        checks['no_answer_on_denial']=not answer and not sources and not cited
    return {'checks':checks,'rule_passed':all(checks.values()),'answer':answer,'candidate_keys':sources,'cited_keys':cited,'agent_status':result.get('agent',{}).get('status'),'tools':result.get('agent',{}).get('steps',[]),'human_review':'待人工核对：事实、否定与引用支持关系'}


def fingerprint(store):
    with store.connect() as db:
        return tuple(tuple(tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')) for table in ('shared_memories','shared_history','shared_audience','shared_evidence'))


def evaluate(dataset,config,folder,progress=None):
    folder=Path(folder);store=MemoryStore(folder/'synthetic.sqlite3')
    retriever=MemoryRetriever(config['retrieval'],folder/'vectors.sqlite3') if config['retrieval']['enabled'] else None
    shared=SharedMemory(store,retriever=retriever);tokens={};spaces={};alias={}
    for name in ('owner','reader','outsider'):
        shared.register({'name':name,'password':'synthetic-evaluation-only'})
        tokens[name]=shared.login({'name':name,'password':'synthetic-evaluation-only'})['token']
    for name in dataset['spaces']:
        spaces[name]=shared.create_space(tokens['owner'],{'name':name,'kind':'team'})['id']
        shared.set_member(tokens['owner'],{'space_id':spaces[name],'name':'reader','role':'reader'})
    for record in dataset['records']:
        payload={k:record[k] for k in ('content','status','event_date','visibility')}
        payload.update(space_id=spaces[record['space']],viewers=[],edit_policy='author',source_note='虚构评测资料 '+record['key'],status_reason='仅为评测设定的状态' if record['status']!='pending' else '')
        plan=shared.preview(tokens['owner'],payload)
        saved=shared.confirm(tokens['owner'],{'space_id':payload['space_id'],'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
        alias[saved['id']]=record['key']
    agent=MemoryAgent(shared,ModelClient(config['llm']));audit=AgentAudit(shared,folder/'diagnostics.sqlite3');rows=[]
    for case in dataset['cases']:
        request={'space_id':spaces[case['space']],'query':case['query'],'include_outdated':case.get('include_outdated',False),'conversation_mode':case.get('conversation_mode',False)}
        token=tokens[case['actor']];before=fingerprint(store);started=time.monotonic();status=200;result={}
        try:result=audit.run(token,request,'ask',lambda:agent.answer(token,request,Cancellation()))
        except SharedError as error:status=error.status
        except Exception:status=500
        elapsed=round((time.monotonic()-started)*1000)
        row={'id':case['id'],'category':case['category'],'actor':case['actor'],'space':case['space'],'query':case['query'],'expected_refs':case['expected_refs'],'expected_facts':case.get('facts',[]),'http_status':status,'elapsed_ms':elapsed,
             **assess(dataset,case,result,status,before!=fingerprint(store),alias)}
        if result.get('diagnostic',{}).get('id'):
            own=audit.list(token,request)['records'];record=next((r for r in own if r['id']==result['diagnostic']['id']),{})
            row.update(usage=record.get('usage'),stages=record.get('steps'))
        rows.append(row)
        if progress:progress(row)
    return rows


def render_html(report):
    escape=lambda value:html.escape(str(value),quote=True)
    rows=[]
    for row in report['cases']:
        failed=[key for key,ok in row['checks'].items() if not ok]
        rows.append('<details open><summary>'+escape(row['id']+' · '+row['category']+' · '+('规则通过' if row['rule_passed'] else '需处理'))+'</summary><p><b>问题：</b>'+escape(row['query'])+'</p><p><b>回答：</b>'+escape(row['answer'])+'</p><p>期望依据：'+escape(row['expected_refs'])+'；实际引用：'+escape(row['cited_keys'])+'；检索候选：'+escape(row['candidate_keys'])+'</p><p>未通过项目：'+escape(failed or '无')+' · 耗时 '+escape(row['elapsed_ms'])+' ms</p><p>'+escape(row['human_review'])+'</p></details>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>忆伴文字评测报告</title><style>body{font:16px/1.8 system-ui;max-width:1000px;margin:35px auto;padding:20px;color:#234137;background:#f6f7f1}details{background:white;border:1px solid #dae3d1;border-radius:12px;margin:16px 0;padding:16px}p{white-space:pre-wrap;overflow-wrap:anywhere}summary{font-weight:bold;cursor:pointer}</style><h1>忆伴文字评测报告</h1><p>'+escape(LIMITATIONS)+'</p><p>模型：'+escape(report['model'])+' · 数据集：'+escape(report['dataset'])+' · '+escape(report['label'])+'</p><p>规则通过 '+str(report['summary']['passed'])+'/'+str(report['summary']['total'])+'；人工复核结果未计入自动通过数。</p><p>数据集SHA256：'+escape(report['dataset_sha256'])+'</p>'+''.join(rows)+'</html>'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'tests/fixtures/agent/text-v1.json')
    parser.add_argument('--config',type=Path,default=ROOT/'config/mac.json')
    parser.add_argument('--output',type=Path,required=True,help='必须是尚不存在的新目录，避免覆盖首轮结果')
    parser.add_argument('--label',default='首次独立样本评测')
    args=parser.parse_args();dataset,digest=load_dataset(args.dataset);config=safe_local_config(load_settings(args.config))
    if args.output.exists():parser.error('报告目录已经存在，请使用新目录以保留前一次结果。')
    args.output.mkdir(parents=True)
    code_files=('tools/evaluate_agent.py','yiban/memory/read_only_agent.py','yiban/memory/memory_agent.py','yiban/memory/answer_evidence.py','yiban/memory/shared_dialogue.py','yiban/memory/shared_memory.py','yiban/memory/retrieval.py')
    code_fingerprints={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in code_files}
    report={'dataset':dataset['version'],'dataset_sha256':digest,'label':args.label,'started':datetime.now().isoformat(timespec='seconds'),'model':config['llm']['model'],'limitations':LIMITATIONS,
            'code_sha256':code_fingerprints,'retrieval':{k:config['retrieval'][k] for k in ('enabled','strategy','model','min_similarity')},'cases':[]}
    def progress(row):
        report['cases'].append(row)
        (args.output/'progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'id':row['id'],'passed':row['rule_passed'],'failures':[k for k,v in row['checks'].items() if not v],'elapsed_ms':row['elapsed_ms']},ensure_ascii=False),flush=True)
    with tempfile.TemporaryDirectory(prefix='yiban-agent-eval-') as folder:evaluate(dataset,config,folder,progress)
    report['summary']={'total':len(report['cases']),'passed':sum(r['rule_passed'] for r in report['cases']),'failed_by_check':dict(Counter(k for r in report['cases'] for k,v in r['checks'].items() if not v))}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (args.output/'index.html').write_text(render_html(report))
    print(json.dumps(report['summary'],ensure_ascii=False));return 0 if report['summary']['passed']==report['summary']['total'] else 1


if __name__=='__main__':raise SystemExit(main())
