"""Reviewed recovery, current ACLs, atomic rollback and durable confirmation receipts."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_read_only_agent as fixture
import test_shared_http as http_fixture
from yiban.memory.shared_memory import SharedError
from yiban.memory.shared_recovery import SharedRecovery
from yiban.memory.shared_backup import SharedBackup


class SharedRecoveryTests(unittest.TestCase):
    setUp=fixture.AgentTests.setUp
    tearDown=fixture.AgentTests.tearDown
    token=fixture.AgentTests.token
    uid=fixture.AgentTests.uid
    member=fixture.AgentTests.member
    payload=fixture.AgentTests.payload
    publish=fixture.AgentTests.publish
    archived=fixture.AgentTests.archived
    stage=fixture.AgentTests.stage
    preview=fixture.AgentTests.preview
    confirm=fixture.AgentTests.confirm
    newest=fixture.AgentTests.newest

    @property
    def service(self):return self.shared.recovery

    def error(self,status,fn,*args,**kwargs):
        with self.assertRaises(SharedError) as caught:fn(*args,**kwargs)
        self.assertEqual(caught.exception.status,status,str(caught.exception))

    def record(self,identity):
        with self.store.connect() as db:return self.shared._record(db,identity)

    def dump(self):
        with self.store.connect() as db:return '\n'.join(db.iterdump())

    def propose(self,operation,who='owner',**data):
        return self.service.dispatch('preview',self.token(who),{'space_id':self.space,'operation':operation,'reason':'虚构资料误操作纠正',**data})

    def commit(self,plan,who='owner',**data):
        return self.service.dispatch('confirm',self.token(who),{'space_id':self.space,'token':plan['token'],
            'confirmed':True,'scope_confirmed':True,'acknowledge_conflicts':True,**data})

    def trash(self,item,who='owner'):
        plan=self.propose('trash',who,id=item['id'],version=item['version']);self.commit(plan,who)
        return next(row for row in self.service.dispatch('list',self.token(who),{'space_id':self.space})['items'] if row['id']==item['id'])

    def edit(self,item,who='owner',**changes):
        data={k:item[k] for k in ('id','version','content','visibility','viewers','edit_policy','event_date','source_note','status','status_reason')}
        data.update(changes)
        plan=self.shared.preview(self.token(who),self.payload(**data))
        self.shared.confirm(self.token(who),{'space_id':self.space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
        return self.record(item['id'])

    def replace(self,item,content='虚构新安排在北门集合'):
        p=self.shared.replacements.preview(self.token('owner'),{'space_id':self.space,'id':item['id'],'version':item['version'],'content':content,'reason':'虚构替代测试'})
        r=self.shared.replacements.confirm(self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True,'replacement_confirmed':True,'acknowledge_conflicts':True})
        return self.record(r['id'])

    def history_id(self,item):
        return self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})['entries'][-1]['id']

    def test_trash_preview_no_write_restore_source_history_and_pending_status(self):
        item=self.archived();history=self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})
        source=self.shared.evidence.read(self.token('owner'),{'space_id':self.space,'id':item['id'],'include_full':True})
        before=self.dump();plan=self.propose('trash',id=item['id'],version=item['version'])
        self.assertEqual(before,self.dump());self.commit(plan)
        self.error(403,self.record,item['id'])
        self.assertEqual(self.shared.question_context(self.token('reader'),self.space,'活动在哪里')['sources'],[])
        trash=self.service.dispatch('list',self.token('owner'),{'space_id':self.space})['items'][0]
        plan=self.propose('trash_restore',trash_id=trash['trash_id'])
        self.assertEqual(plan['history_count'],history['total']);self.assertEqual(plan['source']['text'],source['source_text'])
        self.assertEqual(plan['after'][0]['status'],'pending')
        self.commit(plan)
        restored=self.record(item['id']);self.assertEqual(restored['content'],item['content']);self.assertEqual(restored['version'],item['version']+1)
        self.assertEqual(self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})['total'],history['total']+1)
        self.assertEqual(self.shared.evidence.read(self.token('owner'),{'space_id':self.space,'id':item['id'],'include_full':True})['source_text'],source['source_text'])
        self.assertEqual(self.service.dispatch('list',self.token('owner'),{'space_id':self.space})['items'],[])

    def test_legacy_delete_is_recoverable_and_does_not_destroy_source(self):
        item=self.archived()
        result=self.shared.delete_memory(self.token('editor'),{'space_id':self.space,'id':item['id'],'version':item['version'],'confirmed':True})
        self.assertTrue(result['trashed'])
        trash=self.service.dispatch('list',self.token('editor'),{'space_id':self.space})['items'][0]
        p=self.propose('trash_restore','editor',trash_id=trash['trash_id']);self.assertIsNotNone(p['source']);self.commit(p,'editor')
        self.assertTrue(self.record(item['id'])['has_evidence'])

    def test_readers_and_unrelated_editors_cannot_see_or_restore_trash(self):
        item=self.publish(content='私密资料不能从回收站泄露',visibility='selected',edit_policy='editors',viewers=[self.uid('editor')])
        trash=self.trash(item)
        for who in ('editor','reader'):
            self.assertEqual(self.service.dispatch('list',self.token(who),{'space_id':self.space})['items'],[])
            self.error(403,self.propose,'trash_restore',who,trash_id=trash['trash_id'])
            self.assertEqual(self.service.dispatch('events',self.token(who),{'space_id':self.space})['events'],[])
        self.error(403,self.service.dispatch,'list',self.token('outsider'),{'space_id':self.space})

    def test_removed_viewers_are_not_readded_or_broadened_on_restore(self):
        item=self.publish('editor',content='限定成员资料',visibility='selected',viewers=[self.uid('reader')])
        trash=self.trash(item)
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},remove=True)
        p=self.propose('trash_restore',trash_id=trash['trash_id'])
        self.assertEqual(p['removed_viewers'],['reader']);self.assertNotIn('reader',p['after'][0]['audience'])
        self.commit(p)
        self.assertEqual(self.record(item['id'])['viewers'],[])
        self.member('reader','reader')
        self.assertEqual(self.shared.memories(self.token('reader'),self.space)['memories'],[])

    def test_history_restore_keeps_current_acl_and_source_pending_and_records_operation(self):
        item=self.archived();historic=self.history_id(item)
        changed=self.edit(item,content='新的私密安排',visibility='selected',viewers=[],edit_policy='author',event_date='2026-11-22')
        before=self.dump();p=self.propose('history_restore',id=item['id'],version=changed['version'],history_id=historic)
        self.assertEqual(before,self.dump());self.assertEqual(p['after'][0]['visibility'],'selected');self.assertEqual(p['after'][0]['status'],'pending')
        self.commit(p);actual=self.record(item['id']);self.assertEqual(actual['content'],item['content'])
        self.assertEqual(actual['viewers'],[]);self.assertEqual(actual['event_date'],item['event_date']);self.assertTrue(actual['has_evidence'])
        history=self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})
        self.assertEqual(history['entries'][0]['after']['recovery']['operation'],'history_restore')
        self.assertEqual(self.shared.memories(self.token('reader'),self.space)['memories'],[])
        portable=SharedBackup(self.shared).dispatch('export',self.token('owner'),{'space_id':self.space,'confirmed':True})['document']
        self.assertEqual(portable['history'][-1]['after']['recovery']['operation'],'history_restore')

    def test_unrelated_history_noop_and_replaced_old_record_are_refused(self):
        item=self.publish();other=self.publish(content='另一份资料');hid=self.history_id(other)
        self.error(403,self.propose,'history_restore',id=item['id'],version=item['version'],history_id=hid)
        self.error(400,self.propose,'history_restore',id=item['id'],version=item['version'],history_id=self.history_id(item))
        self.error(403,self.propose,'history_restore','reader',id=item['id'],version=item['version'],history_id=hid)
        self.replace(item);old=self.record(item['id'])
        self.error(409,self.propose,'history_restore',id=old['id'],version=old['version'],history_id=self.history_id(old))

    def test_undo_replacement_preserves_records_evidence_and_corrected_link_audit(self):
        old=self.archived();new=self.replace(old);new=self.edit(new,content='新安排后续补充内容',visibility='selected',viewers=[])
        p=self.propose('undo_replacement',id=new['id'],version=new['version'])
        self.assertEqual([r['status'] for r in p['after']],['pending','outdated'])
        self.assertEqual(p['after'][1]['visibility'],'selected');self.commit(p)
        self.assertEqual(self.record(old['id'])['status'],'pending');self.assertEqual(self.record(new['id'])['content'],'新安排后续补充内容')
        self.assertEqual(self.record(new['id'])['status'],'outdated');self.assertTrue(self.record(old['id'])['has_evidence'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_replacements').fetchone()[0],0)
        event=self.service.dispatch('events',self.token('owner'),{'space_id':self.space})['events'][0]
        self.assertEqual(event['operation'],'undo_replacement');self.assertEqual(len(event['before']),2)
        self.assertEqual(event['before'][0]['id'],old['id']);self.assertEqual(event['before'][1]['id'],new['id'])

    def test_only_tail_edge_can_be_corrected_and_previous_chain_stays_intact(self):
        a=self.publish();b=self.replace(a);c=self.replace(b,'第三次安排在南门')
        self.error(409,self.propose,'undo_replacement',id=b['id'],version=self.record(b['id'])['version'])
        self.commit(self.propose('undo_replacement',id=c['id'],version=c['version']))
        with self.store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT old_id,new_id FROM shared_replacements')],[(a['id'],b['id'])])
        self.assertEqual(self.record(a['id'])['status'],'outdated');self.assertEqual(self.record(b['id'])['status'],'pending')

    def test_undo_requires_access_to_both_nodes_and_is_disabled_for_reader(self):
        old=self.publish();new=self.replace(old)
        for who in ('reader','editor'):
            self.error(403,self.propose,'undo_replacement',who,id=new['id'],version=new['version'])
        visible=self.shared.memories(self.token('reader'),self.space)['memories']
        self.assertTrue(all(not m['can_correct_replacement'] for m in visible))

    def test_preview_session_scope_checks_expiry_cancel_and_changed_permissions(self):
        item=self.publish();plan=self.propose('trash',id=item['id'],version=item['version'])
        self.error(400,self.commit,plan,scope_confirmed=False)
        self.error(403,self.commit,plan,'reader')
        token=self.shared.login({'name':'owner','password':'test-password-2026'})['token']
        self.error(403,self.service.dispatch,'confirm',token,{'space_id':self.space,'token':plan['token'],'confirmed':True,'scope_confirmed':True})
        self.service.plans[plan['token']]['expires']=0;self.error(409,self.commit,plan)
        plan=self.propose('trash',id=item['id'],version=item['version'])
        self.service.dispatch('cancel',self.token('owner'),{'token':plan['token']});self.error(409,self.commit,plan)
        plan=self.propose('trash',id=item['id'],version=item['version']);self.member('reader','editor')
        self.error(409,self.commit,plan)

    def test_repeat_confirm_and_restart_do_not_reapply_or_restore_changed_records(self):
        item=self.publish();trashed=self.trash(item);p=self.propose('trash_restore',trash_id=trashed['trash_id'])
        result=self.commit(p);changed=self.edit(self.record(item['id']),content='确认完成后用户又改了内容')
        self.assertTrue(self.commit(p)['already_applied']);self.assertEqual(self.record(item['id'])['content'],changed['content'])
        reopened=SharedRecovery(self.shared)
        again=reopened.dispatch('confirm',self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True,'scope_confirmed':True})
        self.assertEqual(result['ids'],again['ids']);self.assertTrue(again['already_applied'])

    def test_atomic_rollback_for_trash_and_undo_and_retry(self):
        item=self.archived();p=self.propose('trash',id=item['id'],version=item['version'])
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON shared_recovery_events BEGIN SELECT RAISE(ABORT,'failure'); END")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.commit(p)
        self.assertEqual(before,self.dump());self.assertTrue(self.record(item['id'])['has_evidence'])
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_recovery')
        self.commit(p)
        trash=self.service.dispatch('list',self.token('owner'),{'space_id':self.space})['items'][0]
        p=self.propose('trash_restore',trash_id=trash['trash_id'])
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON shared_recovery_events BEGIN SELECT RAISE(ABORT,'failure'); END")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.commit(p)
        self.assertEqual(before,self.dump())
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_recovery')
        self.commit(p);new=self.replace(self.record(item['id']))
        p=self.propose('undo_replacement',id=new['id'],version=new['version'])
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON shared_recovery_events BEGIN SELECT RAISE(ABORT,'failure'); END")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.commit(p)
        self.assertEqual(before,self.dump())
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_recovery')
        self.commit(p)


    def test_conflict_acknowledgment_and_frozen_payload(self):
        item=self.publish(content='我喜欢咖啡');trash=self.trash(item)
        self.publish(content='我不喜欢咖啡')
        p=self.propose('trash_restore',trash_id=trash['trash_id'])
        self.assertTrue(p['conflicts'])
        self.error(400,self.commit,p,acknowledge_conflicts=False)
        self.commit(p,content='伪造的替换内容',operation='trash')
        self.assertEqual(self.record(item['id'])['content'],'我喜欢咖啡')
        self.assertEqual(self.record(item['id'])['status'],'pending')

    def test_history_and_trash_limits_do_not_silently_drop_data(self):
        item=self.publish()
        with patch.object(self.service,'encode',return_value='x'*24_000_001):
            self.error(400,self.propose,'trash',id=item['id'],version=item['version'])
        self.assertEqual(self.record(item['id'])['content'],item['content'])
        trash=self.trash(item)
        with self.store.connect() as db:
            for i in range(200):
                db.execute('INSERT INTO shared_memories(id,space,author,editor,content,visibility,edit_policy) VALUES (?,?,?,?,?,?,?)',
                    ('capacity-'+str(i),self.space,self.uid('owner'),self.uid('owner'),'占位 '+str(i),'all','author'))
        self.error(400,self.propose,'trash_restore',trash_id=trash['trash_id'])
        self.assertEqual(len(self.service.dispatch('list',self.token('owner'),{'space_id':self.space})['items']),1)

    def test_operation_events_recheck_current_role_and_rollback_history_restore(self):
        item=self.publish('editor',content='历史正文');history=self.history_id(item)
        item=self.edit(item,'editor',content='后来修改')
        p=self.propose('history_restore','editor',id=item['id'],version=item['version'],history_id=history)
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_history_recovery BEFORE INSERT ON shared_recovery_events BEGIN SELECT RAISE(ABORT,'failure'); END")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.commit(p,'editor')
        self.assertEqual(before,self.dump())
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_history_recovery')
        self.commit(p,'editor')
        self.assertEqual(len(self.service.dispatch('events',self.token('editor'),{'space_id':self.space})['events']),1)
        self.member('editor','reader')
        self.assertEqual(self.service.dispatch('events',self.token('editor'),{'space_id':self.space})['events'],[])
        current=self.record(item['id'])
        self.error(403,self.propose,'trash','editor',id=item['id'],version=current['version'])


class RecoveryHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space

    def test_http_routes_and_explicit_confirmation(self):
        owner,reader,space,_=self.setup_space('recovery')
        self.assertEqual(self.post('/recovery/list',{'space_id':space})[0],401)
        self.assertEqual(self.post('/recovery/preview',[],owner)[0],400)
        record=self.post('/memories/list',{'space_id':space},owner)[1]['memories'][0]
        status,plan=self.post('/recovery/preview',{'space_id':space,'operation':'trash','id':record['id'],'version':record['version'],'reason':'虚构测试'},owner)
        self.assertEqual(status,200,plan)
        self.assertEqual(self.post('/recovery/confirm',{'space_id':space,'token':plan['token'],'confirmed':True},owner)[0],400)
        status,result=self.post('/recovery/confirm',{'space_id':space,'token':plan['token'],'confirmed':True,'scope_confirmed':True},owner)
        self.assertEqual(status,200,result)
        trash=self.post('/recovery/list',{'space_id':space},owner)[1]['items'][0]
        status,plan=self.post('/recovery/preview',{'space_id':space,'operation':'trash_restore','trash_id':trash['trash_id'],'reason':'虚构恢复'},owner)
        self.assertEqual(status,200,plan)
        self.assertEqual(self.post('/recovery/confirm',{'space_id':space,'token':plan['token'],'confirmed':True,'scope_confirmed':True},reader)[0],403)
        self.assertEqual(self.post('/recovery/confirm',{'space_id':space,'token':plan['token'],'confirmed':True,'scope_confirmed':True},owner)[0],200)


if __name__=='__main__':unittest.main()
