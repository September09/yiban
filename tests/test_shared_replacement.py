"""Replacement is a confirmed atomic transition, with retained and authorized lineage."""
import json
import sqlite3
import unittest
from unittest.mock import patch
import test_memory_agent as fixture
import test_answer_sources as evidence_fixture
import test_shared_http as http_fixture
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation, Cancelled


class ReplacementTests(unittest.TestCase):
    setUp=fixture.MemoryAgentTests.setUp
    tearDown=fixture.MemoryAgentTests.tearDown
    token=fixture.MemoryAgentTests.token
    uid=fixture.MemoryAgentTests.uid
    member=fixture.MemoryAgentTests.member
    payload=fixture.MemoryAgentTests.payload
    publish=fixture.MemoryAgentTests.publish
    newest=fixture.MemoryAgentTests.newest
    edit=fixture.MemoryAgentTests.edit
    agent=fixture.MemoryAgentTests.agent
    ask=fixture.MemoryAgentTests.ask
    confirm=fixture.MemoryAgentTests.confirm
    dump=fixture.MemoryAgentTests.dump
    error=fixture.MemoryAgentTests.error
    stage=evidence_fixture.AnswerSourceTests.stage
    def original(self,who='owner',**kw):
        return self.publish(who,content='科学活动十点在科技馆正门集合，林老师带队。',event_date='2026-11-21',status='valid',status_reason='已核实',**kw)
    def preview(self,agent,item,who='owner',**kw):
        data=dict(space_id=self.space,id=item['id'],version=item['version'],content='科学活动十一点在科技馆北门集合，林老师带队。',event_date=item['event_date'],reason='负责人确认集合时间和地点变更');data.update(kw)
        return agent.replacement_preview(self.token(who),data)
    def commit(self,agent,plan,**kw):return self.confirm(agent,plan,replacement_confirmed=True,acknowledge_conflicts=True,**kw)
    def item(self,identity):
        with self.store.connect() as db:return self.shared._record(db,identity)
    def lineage(self,item,who='owner'):
        return self.shared.replacements.lineage(self.token(who),dict(space_id=self.space,id=item['id'],version=item['version']))

    def test_preview_cancel_and_independent_consent_never_write(self):
        item=self.original();agent=self.agent();before=self.dump();p=self.preview(agent,item)
        self.assertEqual(before,self.dump());self.assertEqual(p['memory_preview']['operation'],'replace')
        self.error(400,self.confirm,agent,p);self.assertEqual(before,self.dump())
        agent.cancel(self.token('owner'),dict(space_id=self.space,token=p['memory_preview']['token']))
        self.assertFalse(self.shared.replacements.plans);self.assertEqual(before,self.dump());self.error(409,self.commit,agent,p)

    def test_atomic_new_record_statuses_history_frozen_payload_and_single_use(self):
        old=self.original();agent=self.agent();p=self.preview(agent,old)
        p['memory_preview']['after']['content']='tampered response object'
        result=self.commit(agent,p,content='tampered request',visibility='selected')
        new=self.item(result['id']);retained=self.item(old['id'])
        self.assertNotEqual(new['id'],old['id']);self.assertIn('十一点',new['content']);self.assertEqual(new['status'],'valid')
        self.assertEqual(retained['content'],old['content']);self.assertEqual(retained['status'],'outdated')
        self.assertEqual(retained['event_date'],old['event_date']);self.assertEqual(retained['version'],old['version']+1)
        self.assertEqual([x['id'] for x in self.lineage(new)['records']],[old['id'],new['id']])
        for item,total in ((retained,2),(new,1)):
            self.assertEqual(self.shared.history(self.token('owner'),dict(space_id=self.space,id=item['id']))['total'],total)
        self.error(409,self.commit,agent,p)

    def test_admin_keeps_original_author_audience_and_original_evidence(self):
        candidates=self.stage()[:1]
        p=self.importer.preview(self.token('editor'),dict(space_id=self.space,keep_evidence=True,items=[self.payload(content=candidates[0]['content'],evidence_ref=candidates[0]['evidence_ref'],visibility='selected',viewers=[self.uid('reader')])]))
        saved=self.importer.confirm(self.token('editor'),dict(space_id=self.space,token=p['token'],confirmed=True,acknowledge_conflicts=True))
        old=self.newest()
        with self.store.connect() as db:before=[tuple(r) for r in db.execute('SELECT * FROM shared_evidence')]
        agent=self.agent();p=self.preview(agent,old);self.assertEqual(set(p['memory_preview']['audience']),{'owner','editor','reader'})
        new=self.item(self.commit(agent,p)['id']);self.assertEqual(new['author'],self.uid('editor'));self.assertEqual(new['editor'],self.uid('owner'))
        self.assertEqual(new['viewers'],old['viewers']);self.assertFalse(new['has_evidence'])
        with self.store.connect() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM shared_evidence')])
        self.assertTrue(self.item(old['id'])['has_evidence'])

    def test_unauthorized_editors_and_readers_cannot_replace(self):
        old=self.original(edit_policy='editors');agent=self.agent();before=self.dump()
        for who in ('editor','reader','outsider'):self.error(403,self.preview,agent,old,who)
        self.assertEqual(before,self.dump())

    def test_exact_session_expiry_and_scope_version_guard(self):
        old=self.original();agent=self.agent();p=self.preview(agent,old);key=p['memory_preview']['token']
        other=self.shared.login(dict(name='owner',password='test-password-2026'))['token']
        self.error(403,agent.confirm,other,dict(space_id=self.space,token=key,confirmed=True,replacement_confirmed=True))
        agent.pending[key]['expires']=0;self.error(409,self.commit,agent,p)
        p=self.preview(agent,old);self.member('editor','reader');self.error(409,self.commit,agent,p)
        self.assertEqual(self.item(old['id'])['status'],'valid');self.assertFalse(self.shared.replacements.plans)

    def test_two_previews_cannot_branch_and_replaced_old_is_read_only(self):
        old=self.original();agent=self.agent();a=self.preview(agent,old);b=self.preview(agent,old)
        new=self.item(self.commit(agent,a)['id']);self.error(409,self.commit,agent,b)
        retained=self.item(old['id']);self.error(409,self.preview,agent,retained)
        data=self.payload(**{k:retained[k] for k in ('id','version','content','visibility','viewers','edit_policy','event_date','source_note')})
        self.error(409,self.shared.preview,self.token('owner'),dict(data,status='valid',status_reason='试图恢复'))
        for item in (retained,new):self.error(409,self.shared.delete_memory,self.token('owner'),dict(space_id=self.space,id=item['id'],version=item['version'],confirmed=True))
        flags={x['id']:x for x in self.shared.memories(self.token('owner'),self.space)['memories']}
        self.assertFalse(flags[retained['id']]['can_edit']);self.assertFalse(flags[new['id']]['can_delete'])

    def test_chain_and_visibility_change_do_not_disclose_hidden_successor(self):
        old=self.original('editor');agent=self.agent();middle=self.item(self.commit(agent,self.preview(agent,old))['id'])
        latest=self.item(self.commit(agent,self.preview(agent,middle,content='科学活动十二点在科技馆西门集合。'))['id'])
        self.assertEqual(len(self.lineage(latest)['records']),3)
        self.edit(latest,visibility='selected',viewers=[])
        result=self.lineage(self.item(middle['id']),'reader')
        self.assertTrue(result['limited']);self.assertEqual(len(result['records']),2)
        self.assertNotIn(latest['id'],json.dumps(result));self.assertNotIn('西门',json.dumps(result,ensure_ascii=False))

    def test_transaction_failure_rolls_back_entire_transition(self):
        old=self.original();agent=self.agent();p=self.preview(agent,old)
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_replacement BEFORE INSERT ON shared_replacements BEGIN SELECT RAISE(ABORT,'test failure'); END")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.commit(agent,p)
        self.assertEqual(before,self.dump())
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_replacement')
        self.assertTrue(self.commit(agent,p)['saved'])

    def test_outdated_noop_invalid_date_and_missing_reason_are_rejected(self):
        old=self.original();agent=self.agent()
        for kw in ({'reason':''},{'content':old['content']},{'event_date':'2026-02-30'}):self.error(400,self.preview,agent,old,**kw)
        with self.store.connect() as db:db.execute("UPDATE shared_memories SET status='outdated' WHERE id=?",(old['id'],))
        self.error(409,self.preview,agent,old)

    def test_model_replace_and_update_proposal_both_preserve_explicit_replacement(self):
        old=self.original()
        for action in ('replace_memory_preview','update_memory_preview'):
            agent=self.agent(fixture.proposal(action,query='科学活动科技馆'),dict(edits=[dict(old='十点',new='十一点'),dict(old='正门',new='北门')]))
            before=self.dump();p=self.ask(agent,query='用新安排替代科学活动的旧安排：十一点北门集合，其他不变')
            self.assertEqual(p['memory_preview']['operation'],'replace');self.assertEqual(before,self.dump())

    def test_model_cannot_turn_replacement_into_unlinked_save_or_invent_replace(self):
        agent=self.agent(fixture.proposal());p=self.ask(agent,query='用新安排替代旧安排：十一点集合')
        self.assertNotIn('memory_preview',p)
        agent=self.agent(fixture.proposal('replace_memory_preview',query='科学活动'))
        p=self.ask(agent,query='修改科学活动集合时间');self.assertNotIn('memory_preview',p)

    def test_patch_preserves_unrequested_facts_and_blocks_invented_end_time(self):
        from yiban.memory.memory_agent import replacement_content
        old='上午11点集合，预计上午11点30分结束。'
        query='改为上午11点15分集合，其他保持不变'
        raw=json.dumps({'edits':[{'old':'上午11点集合','new':'上午11点15分集合'}]})
        self.assertEqual(replacement_content(raw,old,query),'上午11点15分集合，预计上午11点30分结束。')
        raw=json.dumps({'edits':[{'old':'11点30分','new':'11点45分'}]})
        with self.assertRaises(SharedError):replacement_content(raw,old,query)
        raw=json.dumps({'edits':[{'old':'11点','new':'11点15分'}]})
        with self.assertRaises(SharedError):replacement_content(raw,old,query)

    def test_cancel_after_plan_creation_discards_all_tokens(self):
        old=self.original();agent=self.agent();cancel=Cancellation();guard=self.shared.question_guard(self.token('owner'),self.space)
        p=self.shared.replacements.preview(self.token('owner'),dict(space_id=self.space,id=old['id'],version=old['version'],content='新安排十一点集合',reason='已核实'))
        cancel.cancel()
        with self.assertRaises(Cancelled):agent._replacement_result(self.token('owner'),guard,p,[],cancel)
        self.assertFalse(agent.pending);self.assertFalse(self.shared.replacements.plans)

    def test_conflicts_require_acknowledgement_and_cache_failure_does_not_undo_save(self):
        old=self.original();self.publish(content='我喜欢咖啡')
        agent=self.agent();p=self.preview(agent,old,content='我不喜欢咖啡');self.assertTrue(p['memory_preview']['conflicts'])
        self.error(400,self.confirm,agent,p,replacement_confirmed=True)
        from unittest.mock import Mock
        retriever=Mock();retriever.invalidate.side_effect=RuntimeError('cache unavailable')
        with patch.object(self.shared,'retriever',retriever):result=self.commit(agent,p)
        self.assertTrue(result['saved']);self.assertEqual(self.item(old['id'])['status'],'outdated')

    def test_permission_changes_during_preview_cleanup_all_plans(self):
        old=self.original();agent=self.agent();remember=agent._remember
        def raced(*args,**kwargs):
            identity=remember(*args,**kwargs);self.member('editor','reader');return identity
        with patch.object(agent,'_remember',side_effect=raced):self.error(409,self.preview,agent,old)
        self.assertFalse(agent.pending);self.assertFalse(self.shared.replacements.plans)

    def test_current_retrieval_uses_new_record_history_can_find_old(self):
        old=self.original();agent=self.agent();new=self.commit(agent,self.preview(agent,old))
        current=self.shared.question_context(self.token('reader'),self.space,'科学活动集合安排')['sources']
        self.assertIn(new['id'],[x['id'] for x in current]);self.assertNotIn(old['id'],[x['id'] for x in current])
        historical=self.shared.question_context(self.token('reader'),self.space,'科学活动原安排',include_outdated=True)['sources']
        self.assertIn(old['id'],[x['id'] for x in historical])

    def test_first_old_arrangement_uses_linked_root_not_latest_or_intermediate(self):
        import test_read_only_agent as ro
        old=self.original();agent=self.agent();middle=self.item(self.commit(agent,self.preview(agent,old))['id'])
        latest=self.item(self.commit(agent,self.preview(agent,middle,content='科学活动十二点在科技馆西门集合。'))['id'])
        model=ro.Scripted(ro.step('search_memory','科学活动'),ro.step('read_material',refs=[1,2,3]),ro.step('finish'))
        result=ro.ReadOnlyAgent(self.shared,model).answer(self.token('reader'),dict(space_id=self.space,query='科学活动最初的旧安排是什么？',include_outdated=True),Cancellation())
        self.assertEqual(result['answer_source_ids'],[old['id']]);self.assertIn(old['content'],result['answer']);self.assertEqual(result['source_usage'],'linked_record')
        self.assertNotIn('十二点',result['answer']);self.assertIn('过时',result['answer'])

    def test_missing_chain_root_does_not_guess_initial_arrangement(self):
        import test_read_only_agent as ro
        self.publish(content='科学活动旧安排是十一点集合',status='outdated',status_reason='测试旧版')
        model=ro.Scripted(ro.step('search_memory','科学活动'),ro.step('read_material',refs=[1]),ro.step('finish'))
        result=ro.ReadOnlyAgent(self.shared,model).answer(self.token('reader'),dict(space_id=self.space,query='科学活动最初的旧安排是什么？',include_outdated=True),Cancellation())
        self.assertEqual(result['answer_source_ids'],[]);self.assertIn('不足以确认',result['answer'])


class ReplacementHTTPTests(unittest.TestCase):
    setUpClass=http_fixture.SharedHTTPTests.__dict__['setUpClass']
    tearDownClass=http_fixture.SharedHTTPTests.__dict__['tearDownClass']
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space
    def test_http_preview_confirm_and_lineage(self):
        owner,reader,space,_=self.setup_space('replace')
        item=self.post('/memories/list',dict(space_id=space),reader)[1]['memories'][0]
        data=dict(space_id=space,id=item['id'],version=item['version'],content='新的公开活动安排',reason='测试已核实')
        self.assertEqual(self.post('/agent/memory/replace-preview',data,reader)[0],403)
        status,result=self.post('/agent/memory/replace-preview',data,owner);self.assertEqual(status,200)
        data=dict(space_id=space,token=result['memory_preview']['token'],confirmed=True,acknowledge_conflicts=True)
        self.assertEqual(self.post('/agent/memory/confirm',data,owner)[0],400)
        data['replacement_confirmed']=True;status,result=self.post('/agent/memory/confirm',data,owner);self.assertEqual(status,200)
        status,result=self.post('/memories/lineage',dict(space_id=space,id=result['id'],version=1),reader)
        self.assertEqual(status,200);self.assertEqual([x['status'] for x in result['records']],['outdated','valid'])
