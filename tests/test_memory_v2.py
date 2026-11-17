"""Synthetic structured memories: no production files, identities, audio or model downloads."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.v2_store import StructuredMemory
from yiban.memory.v2_schema import SCHEMA, validate
from yiban.memory.v2_convert import convert
from yiban.memory.v2_query import answer
from yiban.backends.model_settings import REMOTE_REQUEST
from yiban.turns import Cancellation, Cancelled


def packet():
    original='虚构资料：第一届角色甲任班长，第二届角色乙任班长。公益活动征集500本，最终送达460本。对阵乙队38比49失利，对阵丙队未记比分。聚餐举办两场。'
    evidence=[{'source_key':'source-one','start':0,'end':len(original),'text':original}]
    entities=[{'key':k,'kind':'organization' if k in {'class','other','third'} else 'person','name':name,'aliases':[]} for k,name in [('a','角色甲'),('b','角色乙'),('class','甲班'),('other','乙队'),('third','丙队')]]
    events=[{'key':k,'kind':kind,'title':title,'time':{'start':'2026-01-01','precision':'day','basis':'explicit'},'state':'held'} for k,kind,title in [('meal-one','meal','午餐'),('meal-two','meal','晚餐'),('game-one','basketball','对阵乙队'),('game-two','basketball','对阵丙队'),('donation','charity','捐书')]]
    def r(key,kind,title,event=None,fact=None):
        return {'key':key,'type':kind,'title':title,'summary':title,'participants':['a'],'event_key':event,'fact':fact,'evidence':copy.deepcopy(evidence)}
    records=[r('meal-report-one','event','第一次聚餐','meal-one'),r('meal-report-copy','event','第一次聚餐的回顾','meal-one'),r('meal-report-two','event','同日第二次聚餐','meal-two')]
    for who,term,when in [('a','第一届',{'start':'2025-01-01','end':'2025-12-31','precision':'range','basis':'explicit'}),('b','第二届',{'start':'2026-01-01','end':None,'precision':'range','basis':'explicit'})]:
        records.append(r('role-'+who,'fact',term+'班长任职',fact={'subject_key':who,'predicate':'holds_role','value':'班长','term':term,'effective':when}))
    for n,value,metric in [('collected',500,'征集数量'),('delivered',460,'实际送达数量')]:
        records.append(r(n,'fact','捐书'+metric,'donation',{'subject_key':'class','predicate':'quantity','value':value,'unit':'本','metric':metric}))
    for k,other,score in [('game-one','other',{'own':38,'opponent':49,'result':'loss'}),('game-two','third',{'own':None,'opponent':None,'result':'unknown'})]:
        records.append(r(k+'-fact','fact',k+'篮球比分',k,{'subject_key':'class','object_key':other,'predicate':'score','value':score}))
    return {'schema':SCHEMA,'batch_name':'虚构结构化验收','sources':[{'key':'source-one','name':'虚构原文.txt','kind':'original','text':original,'sha256':hashlib.sha256(original.encode()).hexdigest()}], 'entities':entities,'events':events,'records':records}


class FakeModel:
    def __init__(self):self.seen=[];self.callback=None
    def chat(self,messages,**kwargs):
        self.seen.append(messages)
        yield {'message':{'content':json.dumps({'answer':'资料记载了这段经历。','references':[1]},ensure_ascii=False)},'done':False}
        if self.callback:self.callback()
        yield {'message':{'content':''},'done':True}


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=MemoryStore(Path(self.tmp.name)/'test.sqlite3');self.shared=SharedMemory(self.store)
        self.users={}
        for name in ('owner','reader','editor','outsider'):
            self.shared.register({'name':name,'password':'v2-test-password'})
            self.users[name]=self.shared.login({'name':name,'password':'v2-test-password'})
        self.owner=self.users['owner']['token'];self.reader=self.users['reader']['token'];self.editor=self.users['editor']['token']
        self.space=self.shared.create_space(self.owner,{'name':'虚构团队','kind':'team'})['id']
        for name,role in [('reader','reader'),('editor','editor')]:self.shared.set_member(self.owner,{'space_id':self.space,'name':name,'role':role})
        self.model=FakeModel();self.service=StructuredMemory(self.shared,self.model)
        self.scope={'scope_kind':'team','space_id':self.space};self.doc=packet()
    def tearDown(self):self.tmp.cleanup()
    def publish(self,doc=None,token=None,scope=None,**options):
        scope=scope or self.scope;token=token or self.owner
        plan=self.service.preview(token,{**scope,'document':doc or self.doc,'visibility':'all',**options})
        if plan.get('duplicate_only'):return plan
        return self.service.confirm(token,{**scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True})
    def rows(self,token=None,scope=None,**kw):return self.service.listing(token or self.owner,{**(scope or self.scope),**kw})['records']
    def ask(self,query,token=None,**kw):return answer(self.service,token or self.owner,{'query':query,**kw})
    def check_error(self,status,fn,*a,**kw):
        with self.assertRaises(SharedError) as caught:fn(*a,**kw)
        self.assertEqual(caught.exception.status,status)

    def test_preview_is_read_only_confirmation_and_idempotency(self):
        before=self.rows();plan=self.service.preview(self.owner,{**self.scope,'document':self.doc,'visibility':'all'})
        self.assertEqual(before,self.rows());self.check_error(400,self.service.confirm,self.owner,{**self.scope,'token':plan['token']})
        self.publish();self.assertEqual(len(self.rows()),9)
        duplicate=self.publish();self.assertTrue(duplicate['duplicate_only']);self.assertEqual(len(self.rows()),9)
        with self.store.connect() as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_private_account_scope_is_not_a_profile_name_or_team_admin(self):
        self.publish(scope={'scope_kind':'personal'})
        self.assertEqual(len(self.rows(scope={'scope_kind':'personal'})),9)
        self.assertEqual(self.rows(self.reader,{'scope_kind':'personal','profile_id':self.users['owner']['account']['id']}),[])
        self.assertEqual(self.rows(self.reader),[])

    def test_acl_applies_before_candidates_model_and_evidence(self):
        self.publish(visibility='selected')
        self.assertEqual(self.rows(self.reader),[])
        result=self.ask('班长是谁',self.reader);self.assertEqual(result['sources'],[]);self.assertEqual(self.model.seen,[])
        row=self.rows()[0];self.check_error(404,self.service.evidence,self.reader,{**self.scope,'id':row['id']})
        self.check_error(403,self.service.listing,self.users['outsider']['token'],self.scope)

    def test_selected_audience_and_source_body_boundary(self):
        self.publish(visibility='selected',viewers=[self.users['reader']['account']['id']])
        row=self.rows(self.reader)[0]
        quotes=self.service.evidence(self.reader,{**self.scope,'id':row['id']})['quotes'];self.assertTrue(quotes)
        self.check_error(403,self.service.read_trace,self.reader,{**self.scope,'id':row['id']},'source')
        self.assertTrue(self.service.read_trace(self.owner,{**self.scope,'id':row['id']},'source')['sources'])

    def test_reader_cannot_publish_and_spoofed_owner_cannot_authorize(self):
        self.check_error(403,self.service.preview,self.reader,{**self.scope,'document':self.doc,'author':self.users['owner']['account']['id']})
        self.check_error(400,self.service.preview,self.owner,{**self.scope,'document':self.doc,'viewers':['unknown-account']})

    def test_preview_session_scope_membership_and_expiration(self):
        plan=self.service.preview(self.owner,{**self.scope,'document':self.doc})
        self.check_error(403,self.service.confirm,self.editor,{**self.scope,'token':plan['token'],'confirmed':True})
        self.check_error(409,self.service.confirm,self.owner,{'scope_kind':'personal','token':plan['token'],'confirmed':True})
        self.shared.set_member(self.owner,{'space_id':self.space,'name':'reader','role':'editor'})
        self.check_error(409,self.service.confirm,self.owner,{**self.scope,'token':plan['token'],'confirmed':True})
        self.service.plans[plan['token']]['expires']=0
        self.check_error(409,self.service.confirm,self.owner,{**self.scope,'token':plan['token'],'confirmed':True})
        self.assertEqual(self.rows(),[])

    def test_counts_use_event_ids_not_reports_or_day(self):
        self.publish();r=self.ask('我们聚餐过几次')
        self.assertEqual(r['calculation']['groups'][0]['count'],2);self.assertEqual(self.model.seen,[])
        self.assertEqual(len({x['event_key'] for x in r['sources']}),2)

    def test_planned_cancelled_unknown_and_inferred_dates_do_not_count(self):
        for event in self.doc['events']:
            if event['key']=='meal-one':event['state']='planned'
            if event['key']=='meal-two':event['time']['basis']='inferred'
        self.publish();group=self.ask('聚餐几次')['calculation']['groups'][0]
        self.assertEqual(group['count'],0);self.assertEqual(group['uncertain'],1);self.assertEqual(group['excluded'],1)

    def test_personal_attendance_does_not_use_team_activity(self):
        self.publish();r=self.ask('我参加聚餐几次');self.assertEqual(r['sources'],[]);self.assertIn('不能证明你本人出席',r['answer'])

    def test_old_and_current_roles_are_separate_historical_facts(self):
        self.publish()
        self.assertIn('角色甲',self.ask('第一届班长是谁')['answer']);self.assertNotIn('角色乙',self.ask('第一届班长是谁')['answer'])
        self.assertIn('角色乙',self.ask('第二届班长是谁')['answer']);self.assertIn('角色乙',self.ask('现任班长是谁')['answer'])
        self.assertEqual({r['verification'] for r in self.rows() if r['fact'] and r['fact']['predicate']=='holds_role'},{'pending'})

    def test_quantities_keep_units_and_different_reporting_metrics(self):
        self.publish();r=self.ask('捐书多少本');self.assertIn('500本（征集数量）',r['answer']);self.assertIn('460本（实际送达数量）',r['answer'])
        self.assertEqual(len(r['sources']),2)

    def test_scores_do_not_mix_opponents_or_make_up_missing_values(self):
        self.publish();r=self.ask('对阵乙队比分胜负');self.assertIn('38比49',r['answer']);self.assertIn('失利',r['answer']);self.assertNotIn('丙队',r['answer'])
        r=self.ask('丙队比分');self.assertEqual(r['answer'],__import__('yiban.memory.v2_facts',fromlist=['UNKNOWN']).UNKNOWN);self.assertTrue(r['answer_source_ids']);self.assertNotIn('0比0',r['answer'])

    def test_model_generation_rechecks_permissions_after_last_chunk(self):
        self.publish();self.model.callback=lambda:self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},True)
        self.check_error(409,self.ask,'第一次聚餐经历',self.reader)
        self.assertEqual(len(self.model.seen),1)

    def test_update_confirmation_version_archive_and_audit(self):
        self.publish();row=self.rows()[0]
        request={**self.scope,'id':row['id'],'expected_version':row['version'],'changes':{'summary':'修订后的摘要'}}
        plan=self.service.update_preview(self.owner,request)
        self.assertNotEqual(self.rows()[0]['summary'],'修订后的摘要')
        self.service.confirm(self.owner,{**self.scope,'token':plan['token'],'confirmed':True})
        changed=next(x for x in self.rows() if x['id']==row['id']);self.assertEqual(changed['version'],2);self.assertEqual(changed['verification'],'pending')
        self.assertEqual(len(self.service.read_trace(self.owner,{**self.scope,'id':row['id']},'history')['history']),2)
        self.check_error(409,self.service.update_preview,self.owner,request)
        self.check_error(403,self.service.update_preview,self.editor,{**request,'expected_version':2})
        plan=self.service.update_preview(self.owner,{**request,'expected_version':2,'changes':{'lifecycle':'archived'}})
        self.service.confirm(self.owner,{**self.scope,'token':plan['token'],'confirmed':True})
        self.assertEqual(len(self.rows()),8);self.assertEqual(len(self.rows(include_archived=True)),9)

    def test_structured_fact_revision_requires_review_and_updates_query(self):
        self.publish();row=next(r for r in self.rows() if r['key']=='delivered');f=copy.deepcopy(row['fact']);f['value']=450
        plan=self.service.update_preview(self.owner,{**self.scope,'id':row['id'],'expected_version':1,'changes':{'fact':f}})
        self.assertEqual(plan['after']['verification'],'pending')
        self.service.confirm(self.owner,{**self.scope,'token':plan['token'],'confirmed':True})
        self.assertIn('450本',self.ask('捐书多少本')['answer'])

    def test_confidence_is_not_verification_and_private_candidates_are_not_shared(self):
        self.doc['records'][0].update(confidence=1,verification='pending',sensitivity='personal')
        self.check_error(400,self.service.preview,self.owner,{**self.scope,'document':self.doc,'visibility':'all'})
        self.publish(scope={'scope_kind':'personal'});self.assertTrue(all(r['verification']=='pending' for r in self.rows(scope={'scope_kind':'personal'})))

    def test_source_offsets_hashes_cycles_invalid_dates_and_unknown_fields_rejected(self):
        mutations=[lambda d:d['records'][0]['evidence'][0].update(start=1),lambda d:d['sources'][0].update(sha256='bad'),
                   lambda d:d['events'][0].update(parent_key='meal-one'),lambda d:d['events'][0]['time'].update(start='2026-02-30'),
                   lambda d:d['records'][0].update(owner='forged')]
        for mutate in mutations:
            d=copy.deepcopy(self.doc);mutate(d)
            with self.assertRaises(SharedError):validate(d)
        d=copy.deepcopy(self.doc);d['records'][5]['fact']['value']=float('nan')
        with self.assertRaises(SharedError):validate(d)

    def test_converter_does_not_trust_legacy_review_flags_or_local_paths(self):
        d={'source':{'name':'虚构候选.md','path':'/never/read/this'},'cards':[{'title':'夜跑','type':'event','what':'一次夜跑','when':'2025-11-11','when_precision':'day','quote':'一次夜跑','who_names':['角色甲'],'confidence':0.99,'needs_review':False,'visibility':'private'}]}
        converted=convert(d);r=converted['document']['records'][0]
        self.assertEqual(r['verification'],'pending');self.assertEqual(r['sensitivity'],'personal');self.assertEqual(converted['document']['sources'][0]['kind'],'candidate')
        self.assertEqual(converted['document']['events'][0]['time']['basis'],'inferred')
        self.assertEqual(converted['document']['events'][0]['state'],'unknown')

    def test_remote_query_and_cancellation_respect_private_boundary(self):
        self.publish(scope={'scope_kind':'personal'})
        marker=REMOTE_REQUEST.set(True)
        try:self.check_error(400,self.ask,'个人经历')
        finally:REMOTE_REQUEST.reset(marker)
        cancel=Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):answer(self.service,self.owner,{'query':'聚餐'},cancel)

    def test_scope_counts_are_separate_and_ambiguous_team_requires_choice(self):
        self.publish();second=self.shared.create_space(self.owner,{'name':'第二虚构团队','kind':'team'})['id'];self.publish(scope={'scope_kind':'team','space_id':second})
        result=self.ask('聚餐回顾');self.assertEqual(len(result['space_choices']),2)
        result=self.ask('各团队聚餐几次');self.assertEqual([g['count'] for g in result['calculation']['groups']],[2,2])
        self.assertEqual(len(self.ask('聚餐几次',space_choice=second)['calculation']['groups']),1)

    def test_same_named_people_are_not_merged_across_unreviewed_articles(self):
        d={'source':{'name':'甲文'},'cards':[{'title':'跑步','what':'角色甲跑步','type':'event','who_names':['角色甲']}]}
        first=convert(d)['document'];d['source']['name']='乙文'
        second=convert(d)['document'];self.assertNotEqual(first['entities'][0]['key'],second['entities'][0]['key'])

    def test_fact_editor_cannot_resolve_hidden_entity_keys(self):
        self.publish();row=next(r for r in self.rows() if r['key']=='delivered');f=copy.deepcopy(row['fact']);f['subject_key']='b'
        self.check_error(400,self.service.update_preview,self.owner,{**self.scope,'id':row['id'],'expected_version':1,'changes':{'fact':f}})

    def test_concurrent_preview_does_not_double_publish(self):
        a=self.service.preview(self.owner,{**self.scope,'document':self.doc})
        b=self.service.preview(self.owner,{**self.scope,'document':self.doc})
        self.service.confirm(self.owner,{**self.scope,'token':a['token'],'confirmed':True})
        self.check_error(409,self.service.confirm,self.owner,{**self.scope,'token':b['token'],'confirmed':True})
        self.assertEqual(len(self.rows()),9)

    def test_invalid_field_types_return_validation_error(self):
        for field in ('type','verification','event_key','sensitivity'):
            d=copy.deepcopy(self.doc);d['records'][0][field]=[]
            with self.assertRaises(SharedError):validate(d)
        for invalid in ('',[],None,{'schema':SCHEMA}):
            with self.assertRaises(SharedError):validate(invalid)

    def test_event_revision_previews_all_affected_records_then_resets_verification(self):
        self.publish();row=next(r for r in self.rows() if r['key']=='meal-report-one')
        plan=self.service.event_preview(self.owner,{**self.scope,'id':row['id'],'expected_version':1,'changes':{'state':'cancelled'}})
        self.assertEqual(len(plan['items']),2);self.assertEqual(self.ask('聚餐几次')['calculation']['groups'][0]['count'],2)
        self.service.confirm(self.owner,{**self.scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True})
        self.assertEqual(self.ask('聚餐几次')['calculation']['groups'][0]['count'],1)
        linked=[r for r in self.rows() if r['event_key']=='meal-one']
        self.assertEqual([r['version'] for r in linked],[2,2]);self.assertTrue(all(r['verification']=='pending' for r in linked))
        self.check_error(403,self.service.event_preview,self.editor,{**self.scope,'id':row['id'],'expected_version':2,'changes':{'state':'held'}})

    def test_retrieval_receives_only_authorized_snapshot_and_rejects_forged_hits(self):
        self.publish(visibility='selected')
        class Retriever:
            def retrieve(self,query,rows,**kw):
                self.rows=rows;return {'hits':[{'id':'outside-record','version':1}]}
        retriever=Retriever();self.shared.retriever=retriever
        # With no authorized rows the retriever is not called at all.
        self.assertEqual(self.ask('午餐经历',self.reader)['sources'],[])
        self.check_error(409,self.ask,'午餐经历')
        self.assertEqual(len(retriever.rows),2)
        self.assertTrue(all(r["event_key"]=="meal-one" for r in retriever.rows))

    def test_local_semantic_retrieval_preserves_reference_mapping(self):
        self.publish()
        class Retriever:
            def retrieve(self,query,rows,**kw):
                return {'hits':[{'id':rows[0]['id'],'version':rows[0]['version']}]}
        self.shared.retriever=Retriever();result=self.ask('换一种说法的经历问题')
        self.assertEqual(len(result['answer_source_ids']),1);self.assertEqual(result['source_usage'],'model_reported')

    def test_full_dates_ranges_and_as_of_roles_do_not_mix_same_month_events(self):
        self.doc['events'][1]['time']['start']='2026-01-02';self.publish()
        self.assertEqual(self.ask('2026年1月1日聚餐几次')['calculation']['groups'][0]['count'],1)
        self.assertEqual(self.ask('2026-01-01至2026-01-02聚餐几次')['calculation']['groups'][0]['count'],2)
        self.assertIn('角色甲',self.ask('2025年6月1日的班长是谁')['answer'])
        self.assertNotIn('角色乙',self.ask('2025年6月1日的班长是谁')['answer'])

    def test_unicode_offsets_use_codepoints_and_preserve_whitespace(self):
        doc=copy.deepcopy(self.doc);raw='  🐦原文\n'
        doc['sources'][0].update(text=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
        for record in doc['records']:record['evidence']=[{'source_key':'source-one','start':2,'end':5,'text':'🐦原文'}]
        self.assertEqual(validate(doc)['records'][0]['evidence'][0]['text'],'🐦原文')

    def test_chat_write_request_does_not_mutate_or_claim_saved(self):
        self.publish();before=self.rows();result=self.ask('帮我修改班长')
        self.assertEqual(self.rows(),before);self.assertIn('本轮没有写入',result['answer']);self.assertEqual(self.model.seen,[])


if __name__=='__main__':unittest.main()
