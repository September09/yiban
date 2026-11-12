"""Portable space archives preserve records and history only after reviewed restore."""
import copy
import json
import unittest
from pathlib import Path
import tempfile
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory
import test_shared_http as http_fixture
from unittest.mock import patch

import test_read_only_agent as fixture
from yiban.memory.shared_backup import SharedBackup
from yiban.memory.shared_memory import SharedError


class SharedBackupTests(unittest.TestCase):
    setUp = fixture.AgentTests.setUp
    tearDown = fixture.AgentTests.tearDown
    token = fixture.AgentTests.token
    uid = fixture.AgentTests.uid
    member = fixture.AgentTests.member
    payload = fixture.AgentTests.payload
    publish = fixture.AgentTests.publish
    archived = fixture.AgentTests.archived
    stage = fixture.AgentTests.stage
    preview = fixture.AgentTests.preview
    confirm = fixture.AgentTests.confirm
    newest = fixture.AgentTests.newest

    def service(self):
        return SharedBackup(self.shared)

    def export(self, service):
        return service.dispatch('export', self.token('owner'), {'space_id': self.space, 'confirmed': True})['document']

    def preview_restore(self, service, document, **changes):
        data = {'document': document, 'name': '恢复测试空间',
                'mapping': {a['id']: a['name'] for a in document['accounts']}, **changes}
        return service.dispatch('preview', self.token('owner'), data)

    def restore(self, service, plan, **changes):
        return service.dispatch('confirm', self.token('owner'), {'token': plan['token'], 'confirmed': True, 'permissions_confirmed': True, **changes})

    def error(self, status, fn, *args, **kwargs):
        with self.assertRaises(SharedError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def test_export_is_admin_only_and_contains_no_credentials(self):
        service = self.service()
        self.error(403, service.dispatch, 'export', self.token('reader'), {'space_id': self.space, 'confirmed': True})
        self.error(400, service.dispatch, 'export', self.token('owner'), {'space_id': self.space})
        self.publish(content='备份公开记忆', visibility='all')
        self.publish(content='备份指定成员记忆', visibility='selected', viewers=[self.uid('reader')])
        document = self.export(service)
        self.assertEqual(len(document['memories']), 2)
        text = json.dumps(document)
        for private in ('password_hash', 'token_hash', 'salt', self.token('owner')):
            self.assertNotIn(private, text)
        self.assertEqual(service.dispatch('inspect', self.token('owner'), {'document': document})['counts']['memories'], 2)

    def test_roundtrip_preserves_evidence_history_acl_and_replacement_chain(self):
        old = self.archived()
        replacement = self.shared.replacements.preview(self.token('owner'), {'space_id': self.space, 'id': old['id'],
            'version': old['version'], 'content': old['content']+'，新安排在北门集合。', 'reason': '虚构替代测试'})
        self.shared.replacements.confirm(self.token('owner'), {'space_id': self.space, 'token': replacement['token'],
            'confirmed': True, 'replacement_confirmed': True, 'acknowledge_conflicts': True})
        self.publish(content='指定成员保密内容', visibility='selected', viewers=[self.uid('reader')])
        service = self.service(); document = self.export(service)
        original = self.shared.memories(self.token('owner'), self.space)['memories']
        original_count = len(original)
        plan = self.preview_restore(service, document)
        self.assertEqual(len(self.shared.spaces(self.token('owner'))['spaces']), 1)
        result = self.restore(service, plan); target = result['space_id']
        self.assertNotEqual(target, self.space)
        restored = self.shared.memories(self.token('owner'), target)['memories']
        self.assertEqual(len(restored), original_count)
        self.assertEqual({m['content'] for m in restored}, {m['content'] for m in original})
        self.assertTrue({m['id'] for m in restored}.isdisjoint({m['id'] for m in original}))
        restored_old = next(m for m in restored if m['content'] == old['content'])
        detail = self.shared.evidence.read(self.token('owner'), {'space_id': target, 'id': restored_old['id'], 'include_full': True})
        self.assertIn('source_text', detail)
        self.assertEqual(self.shared.history(self.token('owner'), {'space_id': target, 'id': restored_old['id']})['total'],
                         self.shared.history(self.token('owner'), {'space_id': self.space, 'id': old['id']})['total'])
        chain = self.shared.replacements.lineage(self.token('owner'), {'space_id': target, 'id': restored_old['id'], 'version': restored_old['version']})
        self.assertEqual(len(chain['records']), 2)
        self.assertEqual(self.shared.memories(self.token('owner'), self.space)['memories'], original)
        self.assertNotIn('指定成员保密内容', json.dumps(self.shared.memories(self.token('editor'), target), ensure_ascii=False))
        self.assertIn('指定成员保密内容', json.dumps(self.shared.memories(self.token('reader'), target), ensure_ascii=False))
        restored_export = service.dispatch('export', self.token('owner'), {'space_id': target, 'confirmed': True})['document']
        self.assertEqual(len(restored_export['history']), len(document['history']))
        self.assertEqual(restored_export['sources'], document['sources'])
        self.assertEqual(self.restore(service, plan)['space_id'], target)
        again = copy.deepcopy(document); again['exported_at'] = '2026-11-13T00:00:00+00:00'
        self.assertEqual(self.preview_restore(service, again)['duplicate']['space'], target)

    def test_mapping_requires_existing_distinct_accounts_and_current_admin(self):
        self.publish(content='作者映射测试', visibility='all')
        service = self.service(); document = self.export(service)
        mapping = {a['id']: a['name'] for a in document['accounts']}
        changed = dict(mapping); changed[self.uid('reader')] = '不存在的本机账号'
        self.error(400, self.preview_restore, service, document, mapping=changed)
        changed = dict(mapping); changed[self.uid('reader')] = 'owner'
        self.error(400, self.preview_restore, service, document, mapping=changed)
        changed = dict(mapping); changed[self.uid('owner')] = 'reader'
        self.error(400, self.preview_restore, service, document, mapping=changed)

    def test_confirm_requires_checks_session_expiry_and_cancel(self):
        service = self.service(); document = self.export(service)
        plan = self.preview_restore(service, document)
        self.error(400, self.restore, service, plan, permissions_confirmed=False)
        second = self.shared.login({'name': 'owner', 'password': 'test-password-2026'})['token']
        self.error(403, service.dispatch, 'confirm', second, {'token': plan['token'], 'confirmed': True, 'permissions_confirmed': True})
        service.plans[plan['token']]['expires'] = 0
        self.error(409, self.restore, service, plan)
        plan = self.preview_restore(service, document)
        service.dispatch('cancel', self.token('owner'), {'token': plan['token']})
        self.error(409, self.restore, service, plan)

    def test_tampered_evidence_and_broken_relations_are_rejected(self):
        self.archived(); service = self.service(); document = self.export(service)
        broken = copy.deepcopy(document); broken['sources'][0]['text'] += '篡改'
        self.error(400, service.validate, broken)
        broken = copy.deepcopy(document); broken['evidence'][0]['quotes'][0]['end'] = 99999999
        self.error(400, service.validate, broken)
        broken = copy.deepcopy(document); broken['history'][0]['after']['id'] = 'another-record'
        self.error(400, service.validate, broken)
        broken = copy.deepcopy(document); identity = broken['memories'][0]['id']; broken['memories'][0]['status'] = 'outdated'
        broken['replacements'] = [{'old_id': identity, 'new_id': identity, 'actor': self.uid('owner'), 'created': '2026-11-12 10:00:00'}]
        self.error(400, service.validate, broken)

    def test_partial_restore_rolls_back_and_can_be_retried(self):
        self.publish(content='恢复第一条'); self.publish(content='恢复第二条')
        service = self.service(); plan = self.preview_restore(service, self.export(service))
        original = service.remap_record; calls = []
        def fail_after_first(*args):
            calls.append(1)
            if len(calls) == 2:raise RuntimeError('模拟中途失败')
            return original(*args)
        with patch.object(service, 'remap_record', side_effect=fail_after_first):
            with self.assertRaises(RuntimeError):self.restore(service, plan)
        self.assertEqual(len(self.shared.spaces(self.token('owner'))['spaces']), 1)
        self.assertEqual(self.restore(service, plan)['counts']['memories'], 2)


    def test_cross_database_mapping_preserves_membership_and_private_access(self):
        self.archived()
        self.publish('editor', content='移除成员保留署名的记忆', visibility='all')
        self.shared.set_member(self.token('owner'), {'space_id':self.space,'account_id':self.uid('editor')}, remove=True)
        self.publish(content='只给读者的记录', visibility='selected', viewers=[self.uid('reader')])
        source_service=self.service(); document=self.export(source_service)
        with tempfile.TemporaryDirectory() as root:
            shared=SharedMemory(MemoryStore(Path(root)/'target.sqlite3'))
            logins={}
            for name in ('新管理员','新读者','原编辑'):
                shared.register({'name':name,'password':'target-test-password'})
                logins[name]=shared.login({'name':name,'password':'target-test-password'})
            service=SharedBackup(shared); token=logins['新管理员']['token']
            mapping={self.uid('owner'):'新管理员',self.uid('reader'):'新读者',self.uid('editor'):'原编辑'}
            inspection=service.dispatch('inspect',token,{'document':document})
            self.assertEqual(next(a for a in inspection['accounts'] if a['id']==self.uid('editor'))['role'],'historical')
            plan=service.dispatch('preview',token,{'document':document,'name':'异机恢复','mapping':mapping})
            result=service.dispatch('confirm',token,{'token':plan['token'],'confirmed':True,'permissions_confirmed':True})
            self.assertEqual(shared.spaces(logins['原编辑']['token'])['spaces'],[])
            target=result['space_id']; records=shared.memories(token,target)['memories']
            former=next(m for m in records if m['content']=='移除成员保留署名的记忆')
            self.assertEqual(former['author'],logins['原编辑']['account']['id'])
            self.assertTrue(any(m['content']=='只给读者的记录' for m in shared.memories(logins['新读者']['token'],target)['memories']))
            self.assertTrue(all(m['author'] not in mapping for m in records))
            reopened=SharedBackup(shared)
            self.assertEqual(reopened.dispatch('confirm',token,{'token':plan['token'],'confirmed':True,'permissions_confirmed':True})['space_id'],target)

    def test_multiple_sessions_same_backup_confirm_once_and_auth_is_required(self):
        service=self.service(); document=self.export(service)
        plan=self.preview_restore(service,document)
        token=self.shared.login({'name':'owner','password':'test-password-2026'})['token']
        other=service.dispatch('preview',token,{'document':document,'name':'恢复测试空间',
            'mapping':{a['id']:a['name'] for a in document['accounts']}})
        target=self.restore(service,plan)['space_id']
        self.assertEqual(service.dispatch('confirm',token,{'token':other['token'],'confirmed':True,'permissions_confirmed':True})['space_id'],target)
        self.assertEqual(len(self.shared.spaces(token)['spaces']),2)
        for action in ('inspect','preview','confirm','cancel','export'):
            self.error(401,service.dispatch,action,None,{})

    def test_malformed_relations_and_limits_fail_before_writes(self):
        self.archived(); service=self.service(); document=self.export(service)
        for key in ('author','editor','viewers','version'):
            broken=copy.deepcopy(document); broken['memories'][0][key]={}
            self.error(400,service.validate,broken)
        broken=copy.deepcopy(document);broken['members'][0]['account']=[]
        self.error(400,service.validate,broken)
        broken=copy.deepcopy(document);broken['history']*=10001
        self.error(400,service.validate,broken)
        legacy=copy.deepcopy(document)
        for row in legacy['history']:
            for item in (row['before'],row['after']):
                if item:item.pop('status',None);item.pop('status_reason',None)
        self.assertTrue(service.validate(legacy)['history'])
        with self.store.connect() as db:before='\n'.join(db.iterdump())
        self.preview_restore(service,document)
        with self.store.connect() as db:self.assertEqual(before,'\n'.join(db.iterdump()))


class BackupHTTPTests(unittest.TestCase):
    setUpClass=classmethod(http_fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.SharedHTTPTests.tearDownClass.__func__)
    post=http_fixture.SharedHTTPTests.post
    setup_space=http_fixture.SharedHTTPTests.setup_space

    def test_routes_require_auth_review_and_current_session(self):
        owner,reader,space,_=self.setup_space('backup')
        self.assertEqual(self.post('/backup/export',{'space_id':space,'confirmed':True})[0],401)
        self.assertEqual(self.post('/backup/export',{'space_id':space,'confirmed':True},reader)[0],403)
        status,export=self.post('/backup/export',{'space_id':space,'confirmed':True},owner)
        self.assertEqual(status,200,export);document=export['document']
        self.assertEqual(self.post('/backup/inspect',{'document':document},owner)[0],200)
        status,plan=self.post('/backup/preview',{'document':document,'name':'HTTP备份恢复',
            'mapping':{a['id']:a['name'] for a in document['accounts']}},owner)
        self.assertEqual(status,200,plan)
        self.assertEqual(self.post('/backup/confirm',{'token':plan['token'],'confirmed':True},owner)[0],400)
        self.assertEqual(self.post('/backup/confirm',{'token':plan['token'],'confirmed':True,'permissions_confirmed':True},reader)[0],403)
        status,result=self.post('/backup/confirm',{'token':plan['token'],'confirmed':True,'permissions_confirmed':True},owner)
        self.assertEqual(status,200,result)
        self.assertEqual(len(self.post('/memories/list',{'space_id':result['space_id']},reader)[1]['memories']),1)
        self.assertEqual(self.post('/backup/inspect',[],owner)[0],400)
        self.assertEqual(self.post('/backup/export',{},owner,origin='https://example.com')[0],403)

if __name__ == '__main__':unittest.main()
