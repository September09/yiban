"""Joint retrieval never expands identity, ACLs, remote consent or write capabilities."""
from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.request,urllib.error
import test_shared_memory as fixture
import test_shared_http as http_fixture
from yiban.memory.unified_memory import UnifiedMemory
from yiban.memory.personal_retrieval import PersonalMemory
from yiban.memory.event_counts import count_events
from yiban.memory.shared_memory import SharedError
from yiban.backends.model_settings import REMOTE_REQUEST
from yiban.turns import Cancellation


class SpyRetriever:
    def __init__(self):self.seen=[];self.hook=None
    def retrieve(self,query,items,**kwargs):
        self.seen+=list(items)
        if self.hook:self.hook()
        return {'hits':[{'id':x['id'],'version':x['version']} for x in items[:8]],'strategy':'lexical'}
    def invalidate(self,*args):pass

class Model:
    def __init__(self,refs=None):self.seen=[];self.hook=None;self.refs=refs or [1]
    def chat(self,messages,**kwargs):
        self.seen.append(messages)
        if self.hook:self.hook()
        yield {'message':{'content':json.dumps({'answer':'根据相应范围的记录回答。','references':self.refs})},'done':True}

class UnifiedTests(unittest.TestCase):
    setUp=fixture.SharedMemoryTests.setUp
    tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token
    uid=fixture.SharedMemoryTests.uid
    member=fixture.SharedMemoryTests.member
    payload=fixture.SharedMemoryTests.payload
    publish=fixture.SharedMemoryTests.publish

    def service(self):
        r=SpyRetriever();m=Model();p=PersonalMemory(self.store,r,m)
        return UnifiedMemory(self.shared,p,m),r,m

    def ask(self,service,query='个人和团队的活动是什么？',**fields):
        return service.answer(self.token('reader'),dict(query=query,**fields),Cancellation())

    def test_joint_search_active_profile_and_memberships_before_embedding(self):
        a=self.store.create_profile('甲')['id'];b=self.store.create_profile('乙')['id']
        self.store.save(a,'甲的私人咖啡偏好');self.store.save(b,'乙的绝不应出现的记录')
        self.publish(content='大家可见的聚餐地点')
        self.publish(content='只有作者可见的绝不应出现记录',visibility='selected')
        service,r,m=self.service();result=self.ask(service,profile_id=a)
        sent=json.dumps([r.seen,m.seen,result],ensure_ascii=False)
        self.assertIn('甲的私人咖啡偏好',sent);self.assertIn('大家可见的聚餐地点',sent)
        self.assertNotIn('绝不应出现',sent)
        self.assertEqual({s['origin'] for s in result['sources']},{'personal','shared'})

    def test_all_authorized_teams_without_selected_space_but_not_other_team(self):
        second=self.shared.create_space(self.token('owner'),{'name':'另一个已加入团队','kind':'team'})['id']
        self.shared.set_member(self.token('owner'),{'space_id':second,'name':'reader','role':'reader'})
        secret=self.shared.create_space(self.token('owner'),{'name':'不可见团队','kind':'team'})['id']
        for sp,text in [(self.space,'2026年1月1日举行聚餐。'),(second,'2026年2月2日举行聚餐。'),(secret,'不可见的聚餐事实')]:
            plan=self.shared.preview(self.token('owner'),self.payload(space_id=sp,content=text))
            self.shared.confirm(self.token('owner'),{'space_id':sp,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
        service,r,m=self.service();result=self.ask(service,'我们总共聚餐过几次？')
        self.assertEqual(len(result['calculation']['groups']),2)
        self.assertNotIn('不可见',json.dumps(result,ensure_ascii=False));self.assertEqual(m.seen,[])

    def test_invalid_token_never_downgrades_to_unauthenticated_team_search(self):
        service,r,m=self.service()
        with self.assertRaises(SharedError) as e:service.answer('invalid',{'query':'聚餐次数'},Cancellation())
        self.assertEqual(e.exception.status,401);self.assertEqual(r.seen,[]);self.assertEqual(m.seen,[])

    def test_missing_team_login_cannot_read_team_by_name_or_spoofed_ids(self):
        self.publish(content='共同记忆的秘密')
        service,r,m=self.service()
        result=service.answer(None,{'query':'活动在哪里','space_id':self.space,'spaces':[self.space]},Cancellation())
        self.assertEqual(result['sources'],[]);self.assertEqual(r.seen,[])

    def test_remote_profile_is_blocked_before_any_model_or_embedding(self):
        a=self.store.create_profile('甲')['id'];self.store.save(a,'个人绝不发云端')
        service,r,m=self.service();binding=REMOTE_REQUEST.set(True)
        try:
            with self.assertRaises(SharedError):self.ask(service,profile_id=a)
        finally:REMOTE_REQUEST.reset(binding)
        self.assertEqual(r.seen,[]);self.assertEqual(m.seen,[])

    def test_remote_team_query_still_filters_selected_memories(self):
        self.publish(content='全体可见的事实')
        self.publish(content='私密不可出云的事实',visibility='selected',viewers=[self.uid('reader')])
        service,r,m=self.service();binding=REMOTE_REQUEST.set(True)
        try:result=self.ask(service)
        finally:REMOTE_REQUEST.reset(binding)
        self.assertNotIn('私密不可出云',json.dumps([result,r.seen,m.seen],ensure_ascii=False))

    def test_membership_revocation_after_retrieval_blocks_before_generation(self):
        self.publish(content='活动在公园')
        service,r,m=self.service()
        r.hook=lambda:self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},True)
        with self.assertRaises(SharedError):self.ask(service)
        self.assertEqual(m.seen,[])

    def test_member_revoked_during_answer_never_returns_old_data(self):
        self.publish(content='活动在公园')
        service,r,m=self.service();m.hook=lambda:self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},True)
        with self.assertRaises(SharedError):self.ask(service)

    def test_personal_change_during_generation_blocks_combined_answer(self):
        a=self.store.create_profile('甲')['id'];self.store.save(a,'我喜欢茶')
        service,r,m=self.service();m.hook=lambda:self.store.save(a,'新个人信息')
        with self.assertRaises(ValueError):self.ask(service,profile_id=a)

    def test_explicit_game_comparison_displays_read_records_without_omitting_outcomes(self):
        self.publish(content='2026年1月1日对阵72Q，以20:10获胜。')
        self.publish(content='2026年2月2日对阵73Q，以18:25落败。')
        service,r,m=self.service();m.hook=lambda:(_ for _ in ()).throw(AssertionError('not needed'))
        result=self.ask(service,'对阵72Q和对阵73Q的两场比赛，比分和胜负分别是什么？')
        for fact in ('20:10','获胜','18:25','落败'):self.assertIn(fact,result['answer'])
        self.assertEqual(result['source_usage'],'program_selected');self.assertEqual(len(result['answer_source_ids']),2)

    def test_no_writes_even_when_asked_to_save(self):
        service,r,m=self.service()
        with self.store.connect() as db:before=list(db.iterdump())
        result=self.ask(service,'记住：我要保存一条新的团队记忆')
        with self.store.connect() as db:self.assertEqual(before,list(db.iterdump()))
        self.assertIn('没有写入',result['answer']);self.assertEqual(m.seen,[])

    def test_ambiguous_teams_require_choice_without_mixing_their_records(self):
        second=self.shared.create_space(self.token('owner'),{'name':'另一个团队','kind':'team'})['id']
        self.shared.set_member(self.token('owner'),{'space_id':second,'name':'reader','role':'reader'})
        for sp in (self.space,second):
            plan=self.shared.preview(self.token('owner'),self.payload(space_id=sp,content='班长是不同角色。'))
            self.shared.confirm(self.token('owner'),{'space_id':sp,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
        service,r,m=self.service();result=self.ask(service,'班长是谁？')
        self.assertEqual(len(result['space_choices']),2);self.assertEqual(m.seen,[])
        selected=self.ask(service,'班长是谁？',space_choice=second)
        self.assertTrue(all(s['space_id']==second for s in selected['sources']))
        with self.assertRaises(SharedError):self.ask(service,space_choice='foreign-id')

    def test_topic_bound_to_login_profile_and_scope_versions(self):
        a=self.store.create_profile('甲')['id'];b=self.store.create_profile('乙')['id']
        self.publish(content='第一届班长是角色甲')
        service,r,m=self.service();result=self.ask(service,'第一届班长是谁？',profile_id=a)
        with self.assertRaises(SharedError):self.ask(service,'那执行班长呢？',profile_id=b,conversation_id=result['conversation']['id'])
        follow=self.ask(service,'那执行班长呢？',profile_id=a,conversation_id=result['conversation']['id'])
        self.assertIn('第一届',m.seen[-1][0]['content'])
        self.assertTrue(follow['conversation']['id'])


class CountingTests(unittest.TestCase):
    def row(self,key,text,when='',scope='a',note=''):
        return {'id':key,'content':text,'event_date':when,'scope_key':scope,'scope_name':'团队 '+scope,'status':'pending','source_note':note}
    def count(self,rows,query='我们总共聚餐过几次？'):
        return count_events(query,rows,date(2026,11,15))
    def test_complete_snapshot_beyond_top_k_is_used_and_deduplicated(self):
        rows=[self.row(str(i),'当日举行聚餐。',f'2026-01-{i:02d}') for i in range(1,13)]
        rows+=[self.row('dup','另一个报道说聚餐很开心。','2026-01-01')]
        result=self.count(rows);self.assertEqual(result['calculation']['groups'][0]['count'],12)
        self.assertIn('至少12次',result['answer']);self.assertFalse(result['calculation']['complete'])
    def test_preview_cancellation_future_summary_and_missing_date_not_added_as_distinct_events(self):
        rows=[self.row('1','聚餐已经举行','2026-01-01'),self.row('2','下周计划聚餐','2026-02-02'),
              self.row('3','聚餐取消','2026-03-03'),self.row('4','举行聚餐','2027-01-01'),
              self.row('5','我们已经聚餐3次'),self.row('6','举行期末聚餐',note='M1｜S1｜期末联欢')]
        result=self.count(rows);g=result['calculation']['groups'][0]
        self.assertEqual(g['count'],1);self.assertEqual(g['record_groups'],2);self.assertEqual(g['undated_groups'],1)
        self.assertIn('不能代表实际全部次数',result['answer'])
    def test_different_scopes_are_not_added_together(self):
        result=self.count([self.row('1','聚餐','2026-01-01'),self.row('2','聚餐','2026-01-01',scope='b')])
        self.assertEqual([g['count'] for g in result['calculation']['groups']],[1,1])
    def test_query_year_and_explicit_date_are_respected(self):
        rows=[self.row('1','聚餐','2025-01-01'),self.row('2','聚餐','2026-01-01'),self.row('3','聚餐','2026-02-02')]
        self.assertEqual(self.count(rows,'2026年聚餐过几次？')['calculation']['groups'][0]['count'],2)
        self.assertEqual(self.count(rows,'2026年1月1日聚餐过几次？')['calculation']['groups'][0]['count'],1)
    def test_month_range_and_personal_attendance_are_not_conflated(self):
        rows=[self.row('1','聚餐','2026-01-01'),self.row('2','聚餐','2026-02-02'),self.row('3','聚餐','2026-03-03')]
        self.assertEqual(self.count(rows,'2026年2月聚餐过几次？')['calculation']['groups'][0]['count'],1)
        self.assertEqual(self.count(rows,'2026年1月1日到2026年3月3日聚餐几次？')['calculation']['groups'][0]['count'],3)
        result=self.count(rows,'我参加过几次聚餐？')
        self.assertEqual(result['sources'],[]);self.assertIn('不能直接证明你本人出席',result['answer'])

    def test_empty_does_not_claim_zero_actual_events(self):
        result=self.count([]);self.assertIn('不能据此认定',result['answer']);self.assertEqual(result['sources'],[])
    def test_noncount_question_is_not_hijacked(self):
        self.assertIsNone(self.count([],query='聚餐地点在哪里？'))


class UnifiedHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    setup_space=http_fixture.SharedHTTPTests.setup_space
    post=http_fixture.SharedHTTPTests.post

    def request(self,data,token=None,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if origin:headers['Origin']=origin
        req=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/companion/ask',data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_http_auth_before_count_and_no_cross_origin(self):
        owner,reader,space,_=self.setup_space('automatic')
        plan=self.post('/memories/preview',{'space_id':space,'content':'2026年1月1日举行聚餐。','event_date':'2026-01-01','visibility':'all','edit_policy':'author'},owner)[1]
        self.post('/memories/confirm',{'space_id':space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True},owner)
        status,result=self.request({'query':'我们聚餐过几次？'},reader)
        self.assertEqual(status,200);self.assertEqual(result['calculation']['groups'][0]['count'],1)
        self.assertEqual(self.request({'query':'聚餐次数'},'invalid')[0],401)
        self.assertEqual(self.request({'query':'聚餐次数'},reader,'https://example.com')[0],403)
