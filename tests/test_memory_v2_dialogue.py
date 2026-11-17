"""Synthetic topic, source, reset and ACL regression tests; no real data/audio."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.v2_store import StructuredMemory
from yiban.memory.v2_schema import SCHEMA
from yiban.memory.v2_query import answer
from yiban.turns import Cancellation,Cancelled


def packet():
    text='周宁用海边带回的贝壳制作贝壳盒，送给岚岚和青禾，纪念星桥6远足。沈溪设计晚宴胸针。'
    events=[{'key':k,'title':title,'kind':'other','state':'held','time':{'precision':'unknown'}} for k,title in [('walk','星桥6远足'),('dinner','创意晚宴')]]
    def row(k,title,summary,event):
        return {'key':k,'type':'reflection','title':title,'summary':summary,'event_key':event,'evidence':[{'source_key':'source','start':0,'end':len(text),'text':text}]}
    return {'schema':SCHEMA,'batch_name':'虚构追问资料','sources':[{'key':'source','name':'虚构文章','kind':'original','text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}],
            'events':events,'entities':[],'records':[row('walk','星桥6远足完成','星桥6远足顺利结束。','walk'),
            row('gift','贝壳盒作为同行纪念','周宁用带回的贝壳制作贝壳盒，送给岚岚和青禾。','walk'),
            row('other','晚宴胸针设计','沈溪设计晚宴胸针。','dinner')]}


class Model:
    def __init__(self):self.seen=[];self.callback=None
    def chat(self,messages,**kwargs):
        self.seen.append(messages)
        rows=json.loads(messages[0]['content'].rsplit('\n',1)[-1])
        target=next((r for r in rows if '贝壳盒作为' in r['title']),rows[0])
        body=target['content']
        if self.callback:self.callback()
        yield {'message':{'content':json.dumps({'answer':body,'references':[target['reference']]},ensure_ascii=False)},'done':True}


class V2FollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=MemoryStore(Path(self.tmp.name)/'test.sqlite3');self.shared=SharedMemory(self.store)
        self.users={}
        for name in ('owner','reader'):
            self.shared.register({'name':name,'password':'test-followup-password'})
            self.users[name]=self.shared.login({'name':name,'password':'test-followup-password'})
        self.token=self.users['owner']['token'];self.space=self.shared.create_space(self.token,{'name':'虚构小组','kind':'team'})['id']
        self.shared.set_member(self.token,{'space_id':self.space,'name':'reader','role':'reader'})
        self.model=Model();self.service=StructuredMemory(self.shared,self.model);self.scope={'scope_kind':'team','space_id':self.space};self.doc=packet()
        self.publish()
    def tearDown(self):self.tmp.cleanup()
    def publish(self,**kw):
        plan=self.service.preview(self.token,{**self.scope,'document':self.doc,'visibility':'all',**kw})
        self.service.confirm(self.token,{**self.scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True})
    def ask(self,q,previous=None,token=None,**kw):
        return answer(self.service,token or self.token,{'query':q,'conversation_id':previous,**kw})
    def fails(self,fn,status=409):
        with self.assertRaises(SharedError) as caught:fn()
        self.assertEqual(caught.exception.status,status)
    def first(self):return self.ask('星桥6结束后留下过什么特别的纪念吗？')

    def confirm_gift(self):
        row=next(r for r in self.service.listing(self.token,self.scope)['records'] if r['key']=='gift')
        plan=self.service.update_preview(self.token,{**self.scope,'id':row['id'],'expected_version':row['version'],
              'changes':{'verification':'confirmed','verification_reason':'Synthetic reviewer checked the original.'}})
        self.service.confirm(self.token,{**self.scope,'token':plan['token'],'confirmed':True})

    def test_verified_simple_followups_use_current_record_and_no_model(self):
        self.confirm_gift()
        self.model.chat=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No model needed for one reviewed statement'))
        result=self.first()
        for query in ['他送给谁了？','那是谁制作的？','当时送出了几个贝壳盒？','所以是不是送了两个贝壳盒？']:
            result=self.ask(query,result['conversation']['id'])
            self.assertTrue(result['answer_source_ids']);self.assertEqual(len(result['answer_source_ids']),1)
            if '谁' in query:
                self.assertEqual(result['answer'],'周宁用带回的贝壳制作贝壳盒，送给岚岚和青禾。')
                self.assertEqual(result['answer_method'],'verified_excerpt')
            else:self.assertEqual(result['answer'],__import__('yiban.memory.v2_facts',fromlist=['UNKNOWN']).UNKNOWN)

    def test_pending_simple_followup_still_uses_model_and_exact_excerpts(self):
        result=self.ask('他送给谁了？',self.first()['conversation']['id'])
        self.assertNotIn('answer_method',result);self.assertEqual(len(self.model.seen),1)
        self.assertIn('original_excerpt',self.model.seen[0][0]['content'])

    def test_verified_excerpt_rechecks_permissions_after_evidence_read(self):
        self.confirm_gift();first=self.first();original=self.service.evidence
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            self.shared.set_member(self.token,{'space_id':self.space,'name':'reader','role':'editor'})
            return result
        self.service.evidence=changed
        self.fails(lambda:self.ask('那是谁制作的？',first['conversation']['id']))
        self.assertEqual(self.model.seen,[])

    def test_conflicting_record_outside_retrieval_hits_prevents_fast_answer(self):
        other=copy.deepcopy(self.doc['records'][1]);other.update(key='competing',summary='陈霖制作贝壳盒，送给青禾。')
        self.doc['records'].append(other);self.publish();self.confirm_gift()
        class TopGift:
            def retrieve(self,q,items,**kw):
                row=next(r for r in items if r['key']=='gift')
                return {'hits':[{'id':row['id'],'version':row['version']}]}
        self.shared.retriever=TopGift();first=self.first();self.model.seen.clear()
        result=self.ask('那是谁制作的？',first['conversation']['id'])
        self.assertNotIn('answer_method',result);self.assertTrue(self.model.seen)

    def test_event_expansion_recovers_related_record_and_excludes_other_event(self):
        class TopOnly:
            def retrieve(self,q,items,**kw):
                base=next(r for r in items if r['key']=='walk')
                return {'hits':[{'id':base['id'],'version':base['version']}]}
        self.shared.retriever=TopOnly()
        r=self.first();self.assertIn('贝壳盒',r['answer']);self.assertNotIn('沈溪',r['answer'])
        self.assertEqual({x['event_key'] for x in r['sources']},{'walk'})
        self.assertEqual(self.model.seen,[])
        self.ask('那是谁制作的？',r['conversation']['id'])
        self.assertIn('original_excerpt',self.model.seen[-1][0]['content'])
        self.assertTrue(r['conversation']['id'])

    def test_unique_title_overview_bypasses_model_latency_and_false_refusal(self):
        def refuse(*a,**kw):
            raise AssertionError('A uniquely reviewed overview must not call the model')
            yield
        self.model.chat=refuse
        r=self.first();self.assertIn('贝壳盒',r['answer']);self.assertTrue(r['answer_source_ids'])
        self.assertEqual(r['source_usage'],'program_selected')

    def test_unique_overview_rejects_invented_count_even_with_valid_reference(self):
        def invented(*a,**kw):
            rows=json.loads(a[0][0]['content'].rsplit('\n',1)[-1])
            row=next(r for r in rows if '贝壳盒作为' in r['title'])
            yield {'message':{'content':json.dumps({'answer':'周宁制作了两个贝壳盒，送给岚岚和青禾。','references':[row['reference']]},ensure_ascii=False)},'done':True}
        self.model.chat=invented
        r=self.first()
        self.assertTrue(r['answer'].endswith('相关记忆记载：'+self.doc['records'][1]['summary']))
        self.assertNotIn('两个',r['answer'])
        self.assertEqual(r['source_usage'],'program_selected')
        cited=[row for row in r['sources'] if row['id'] in r['answer_source_ids']]
        self.assertEqual([row['key'] for row in cited],['gift'])

    def test_pronouns_stay_with_event_and_unknown_quantity_has_evidence(self):
        a=self.first();b=self.ask('他送给谁了？',a['conversation']['id'])
        self.assertIn('青禾',b['answer']);n=len(self.model.seen)
        c=self.ask('当时送出了几个贝壳盒？',b['conversation']['id'])
        self.assertEqual(c['answer'],__import__('yiban.memory.v2_facts',fromlist=['UNKNOWN']).UNKNOWN);self.assertTrue(c['answer_source_ids']);self.assertEqual(n,len(self.model.seen))
        d=self.ask('那是谁制作的？',c['conversation']['id'])
        self.assertIn('周宁',d['answer']);self.assertNotIn('沈溪',d['answer'])
        self.assertEqual({x['event_key'] for x in d['sources']},{'walk'})

    def test_leading_quantity_not_inferred_from_two_recipients(self):
        a=self.first();b=self.ask('所以是不是送了两个贝壳盒？',a['conversation']['id'])
        self.assertEqual(b['answer'],__import__('yiban.memory.v2_facts',fromlist=['UNKNOWN']).UNKNOWN);self.assertTrue(b['answer_source_ids'])

    def test_bare_pronoun_without_topic_asks_clarification(self):
        r=self.ask('那是谁制作的？');self.assertIn('请先说明',r['answer']);self.assertEqual(self.model.seen,[]);self.assertEqual(r['sources'],[])

    def test_explicit_other_event_switches_topic(self):
        a=self.first();b=self.ask('那创意晚宴胸针是谁设计的？',a['conversation']['id'])
        self.assertIn('沈溪',b['answer']);self.assertNotIn('周宁',b['answer'])

    def test_topic_bound_to_token_and_requested_scope(self):
        r=self.first();identity=r['conversation']['id']
        self.fails(lambda:self.ask('那是谁制作的？',identity,token=self.users['reader']['token']))
        self.fails(lambda:self.ask('那是谁制作的？',identity,scope_kind='personal'))

    def test_changed_membership_invalidates_topic_before_model(self):
        a=self.first();n=len(self.model.seen)
        self.shared.set_member(self.token,{'space_id':self.space,'name':'reader','role':'editor'})
        self.fails(lambda:self.ask('那是谁制作的？',a['conversation']['id']));self.assertEqual(len(self.model.seen),n)

    def test_reset_and_expiry_do_not_reuse_old_topic(self):
        a=self.first();self.service.dispatch('reset',self.token,{'conversation_id':a['conversation']['id']})
        self.fails(lambda:self.ask('那是谁制作的？',a['conversation']['id']))
        a=self.first();self.service.dialogue._entries[a['conversation']['id']]['expires']=0
        self.fails(lambda:self.ask('那是谁制作的？',a['conversation']['id']))

    def test_one_turn_identity_cannot_be_replayed(self):
        a=self.first();self.ask('那是谁制作的？',a['conversation']['id'])
        self.fails(lambda:self.ask('他送给谁了？',a['conversation']['id']))

    def test_permission_change_during_generation_is_rejected(self):
        self.model.callback=lambda:self.shared.set_member(self.token,{'space_id':self.space,'name':'reader','role':'editor'})
        self.fails(lambda:self.ask('星桥6纪念品是谁制作的？'))
        self.assertEqual(self.service.dialogue._entries,{})

    def test_human_rejected_records_stay_in_review_but_do_not_answer(self):
        for row in self.service.listing(self.token,self.scope)['records']:
            plan=self.service.update_preview(self.token,{**self.scope,'id':row['id'],'expected_version':row['version'],
                  'changes':{'verification':'disputed','verification_reason':'人工标为不正确，待修改。'}})
            self.service.confirm(self.token,{**self.scope,'token':plan['token'],'confirmed':True})
        r=self.first();self.assertEqual(r['sources'],[]);self.assertEqual(self.model.seen,[])
        self.assertEqual(len(self.service.listing(self.token,self.scope)['records']),3)

    def test_cancelled_query_does_not_save_topic(self):
        cancel=Cancellation();cancel.cancel()
        with self.assertRaises(Cancelled):answer(self.service,self.token,{'query':'星桥6有什么纪念'},cancel)
        self.assertEqual(self.service.dialogue._entries,{})

    def test_topics_keep_only_ids_labels_and_never_answers_or_excerpts(self):
        r=self.first();entry=self.service.dialogue._entries[r['conversation']['id']]
        value=json.dumps(entry,ensure_ascii=False)
        self.assertNotIn('周宁',value);self.assertNotIn('原文',value);self.assertNotIn('海边',value)

    def test_named_event_ambiguity_can_be_selected(self):
        doc=packet();doc['events'][0]['key']='walk-two';doc['records']=[copy.deepcopy(doc['records'][0])]
        doc['records'][0].update(key='walk-two-record',event_key='walk-two')
        self.doc=doc;self.publish()
        a=self.first();self.assertTrue(a['conversation']['needs_clarification']);choice=a['conversation']['choices'][0]
        b=self.ask('星桥6有什么纪念',a['conversation']['id'],choice_id=choice['id'])
        self.assertEqual(len({r['event_key'] for r in b['sources']}),1)

if __name__=='__main__':unittest.main()
