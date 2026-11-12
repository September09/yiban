"""Content-free local diagnostics. Not an atomic or tamper-proof business audit ledger."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from yiban.conversation_diagnostics import TRACES, CURRENT, STAGES
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancelled

TOOL_NAMES={'search_memory','read_material','get_current_time','invalid_request','save_memory_preview','update_memory_preview','replace_memory_preview'}
STATUSES={'running','done','needs_confirmation','needs_selection','needs_clarification','needs_details','failed','timeout','cancelled','saved','rejected','interrupted'}
REASONS={'ok','waiting_confirmation','choose_target','need_details','model_or_tool','timeout','cancelled','permission','stale','invalid_request','unexpected','service_restarted','result_unrecorded'}


def clean_trace(trace):
    steps=[]
    for step in trace.get('steps',[])[:64]:
        if step.get('stage') not in STAGES or step.get('status') not in {'running','done','error','cancelled'}:continue
        steps.append({'stage':step['stage'],'status':step['status'],'elapsed_ms':bounded(step.get('elapsed_ms'))})
    raw=trace.get('usage') or {}
    usage={key:(raw[key] if type(raw.get(key)) is int and 0<=raw[key]<=1_000_000_000_000 else None) for key in ('model_calls','reported_calls','input_tokens','output_tokens')}
    usage['model_calls']=usage['model_calls'] or 0
    usage['reported_calls']=min(usage['reported_calls'] or 0,usage['model_calls'])
    usage['complete']=usage['model_calls']>0 and usage['reported_calls']==usage['model_calls']
    if not usage['reported_calls']:usage['input_tokens']=usage['output_tokens']=None
    return {'steps':steps,'usage':usage}


def bounded(value):return min(value,86400000) if type(value) is int and value>=0 else None


class AgentAudit:
    def __init__(self,shared,path,limit=1000,retention_days=14,clock=time.time):
        self.shared=shared;self.path=Path(path);self.limit=limit;self.retention=retention_days*86400;self.clock=clock
        self.active={};self.lock=threading.Lock();self.ready=False
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connect() as db:
                db.executescript('''CREATE TABLE IF NOT EXISTS events(
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, space TEXT NOT NULL, operation TEXT NOT NULL,
                    started REAL NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                    elapsed_ms INTEGER, details TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS events_owner ON events(actor,space,started DESC);
                    CREATE TABLE IF NOT EXISTS links(digest TEXT PRIMARY KEY, actor TEXT NOT NULL, space TEXT NOT NULL, operation TEXT NOT NULL, created REAL NOT NULL);''')
                db.execute("UPDATE events SET status='interrupted',reason='service_restarted',elapsed_ms=NULL WHERE status='running'")
                self.prune(db)
            self.path.chmod(0o600);self.ready=True
        except OSError:pass
        except sqlite3.Error:pass

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=.3);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def prune(self,db):
        db.execute('DELETE FROM events WHERE started<?',(self.clock()-self.retention,))
        db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY started DESC,rowid DESC LIMIT -1 OFFSET ?)',(self.limit,))
        db.execute('DELETE FROM links WHERE created<? OR operation NOT IN (SELECT operation FROM events)',(self.clock()-600,))

    @staticmethod
    def digest(value):return hashlib.sha256(value.encode()).hexdigest() if isinstance(value,str) and len(value)<=128 else None

    def begin(self,guard,action,data):
        if not self.ready:return None
        identity=str(uuid.uuid4());operation=identity;digest=self.digest(data.get('selection_token') or data.get('token'))
        with self.connect() as db:
            self.prune(db)
            if digest:
                row=db.execute('SELECT operation FROM links WHERE digest=? AND actor=? AND space=?',(digest,guard['actor'],guard['space'])).fetchone()
                if row:operation=row['operation']
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,guard['actor'],guard['space'],operation,self.clock(),action,'running','ok',None,'{}'))
            self.prune(db)
        return {'id':identity,'operation':operation}

    def finish(self,event,guard,result,error,trace,elapsed):
        if not event:return False
        detail=clean_trace(trace);result=result if isinstance(result,dict) else {}
        if error:
            if isinstance(error,Cancelled):status,reason='cancelled','cancelled'
            elif isinstance(error,SharedError):status,reason='rejected',{401:'permission',403:'permission',409:'stale'}.get(error.status,'invalid_request')
            else:status,reason='failed','unexpected'
        else:
            status=result.get('agent',{}).get('status','done')
            if result.get('saved') is True:status='saved'
            if result.get('cancelled') is True:status='cancelled'
            if status not in STATUSES:status='failed'
            reason={'needs_confirmation':'waiting_confirmation','needs_selection':'choose_target','needs_clarification':'choose_target',
                    'needs_details':'need_details','failed':'model_or_tool','timeout':'timeout','cancelled':'cancelled'}.get(status,'ok')
        tools=[]
        for step in result.get('agent',{}).get('steps',[])[:8]:
            if step.get('name') in TOOL_NAMES and step.get('status') in {'running','done','failed','blocked','cancelled'}:
                tools.append({'name':step['name'],'status':step['status'],'elapsed_ms':bounded(step.get('elapsed_ms')),'count':bounded(step.get('count'))})
        detail.update(tools=tools,source_count=min(len(result.get('sources',[])),200),citation_status=result.get('source_usage') if result.get('source_usage') in {'model_reported','unavailable','not_needed'} else None)
        preview=result.get('memory_preview') or result.get('memory_selection') or {}
        with self.connect() as db:
            changed=db.execute('UPDATE events SET status=?,reason=?,elapsed_ms=?,details=? WHERE id=?',(status,reason,bounded(elapsed),json.dumps(detail),event['id'])).rowcount
            if not changed:return False
            digest=self.digest(preview.get('token'))
            if digest:db.execute('INSERT OR REPLACE INTO links VALUES (?,?,?,?,?)',(digest,guard['actor'],guard['space'],event['operation'],self.clock()))
        return True

    def run(self,token,data,action,callback):
        if not isinstance(data,dict):raise SharedError('请求格式不正确。')
        if action not in {'ask','select','read','confirm','cancel'}:raise ValueError('Unknown diagnostic action')
        guard=self.shared.question_guard(token,data.get('space_id'))
        event=None;result=None;error=None;available=False;started=time.monotonic()
        try:event=self.begin(guard,action,data)
        except Exception:pass
        # This identity is never returned as a public trace capability.
        identity=TRACES.begin(str(uuid.uuid4()));context=CURRENT.set(identity)
        if event:
            with self.lock:self.active[event['id']]=identity
        try:
            result=callback()
        except BaseException as caught:
            error=caught
        finally:
            trace=TRACES.read(identity);CURRENT.reset(context)
            try:available=self.finish(event,guard,result,error,trace,round((time.monotonic()-started)*1000))
            except Exception:
                # A completed business save must never be turned into an apparent failure.
                try:
                    with self.connect() as db:
                        db.execute("UPDATE events SET status='interrupted',reason='result_unrecorded' WHERE id=?",(event['id'],))
                except Exception:pass
            if event:
                with self.lock:self.active.pop(event['id'],None)
        diagnostic={'available':available,'id':event['id'] if event and available else None}
        if error:
            error.agent_diagnostic=diagnostic
            raise error
        if isinstance(result,dict):result={**result,'diagnostic':diagnostic}
        return result

    def list(self,token,data):
        if not isinstance(data,dict):raise SharedError('请求格式不正确。')
        guard=self.shared.question_guard(token,data.get('space_id'))
        if not self.ready:return {'available':False,'records':[]}
        try:
            with self.connect() as db:
                self.prune(db)
                rows=db.execute('SELECT id,operation,started,action,status,reason,elapsed_ms,details FROM events WHERE actor=? AND space=? ORDER BY started DESC,rowid DESC LIMIT 50',(guard['actor'],guard['space'])).fetchall()
            records=[]
            for row in rows:
                record=dict(row);record.update(json.loads(record.pop('details')))
                with self.lock:identity=self.active.get(record['id'])
                if record['status']=='running' and identity:
                    record.update(clean_trace(TRACES.read(identity)));record['elapsed_ms']=bounded(round((self.clock()-record['started'])*1000))
                records.append(record)
            self.shared.validate_answer(token,guard)
            return {'available':True,'records':records,'limit':50,'retention_days':self.retention//86400}
        except (OSError,sqlite3.Error,json.JSONDecodeError):return {'available':False,'records':[]}
