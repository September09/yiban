"""Synthetic materials, isolated stores and HTTP; no real user data or audio."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock
import test_material_import as fixture
from yiban.memory.shared_memory import SharedError
from yiban.memory.shared_batches import SharedBatches
from yiban.memory.shared_backup import SharedBackup


class BatchTests(unittest.TestCase):
    setUp=fixture.MaterialTests.setUp
    tearDown=fixture.MaterialTests.tearDown
    token=fixture.MaterialTests.token
    uid=fixture.MaterialTests.uid
    member=fixture.MaterialTests.member
    payload=fixture.MaterialTests.payload
    publish=fixture.MaterialTests.publish

    def error(self, status, fn, *args, **kwargs):
        with self.assertRaises(SharedError) as caught:fn(*args, **kwargs)
        self.assertEqual(caught.exception.status,status,str(caught.exception))

    def call(self, action, who='owner', **data):
        return self.shared.batches.dispatch(action,self.token(who),{'space_id':self.space,**data})

    def make(self, who='owner', text='周六十点在公园集合。\n\n甲负责带物资。', keep=True, **changes):
        draft=self.material.organize(self.token(who),{'space_id':self.space,'sources':[{'name':'活动通知.txt','text':text}]})
        items=[{**self.payload(content=c['content']), 'evidence_ref':c['evidence_ref'],**changes} for c in draft['candidates']]
        plan=self.material.preview(self.token(who),{'space_id':self.space,'items':items,'keep_evidence':keep,'batch_name':'十一月活动纪要','mode':'paragraphs'})
        result=self.material.confirm(self.token(who),{'space_id':self.space,'token':plan['token'],'confirmed':True})
        return result['batch_id'],self.call('detail',who,batch_id=result['batch_id'])['items']

    def propose(self, batch, who='owner'):
        return self.call('preview',who,batch_id=batch,reason='导入了错误版本，先撤销后核对')

    def confirm(self, plan, who='owner', **changes):
        return self.call('confirm',who,**dict({'token':plan['token'],'confirmed':True,'scope_confirmed':True},**changes))

    def edit(self, item):
        with self.store.connect() as db:record=self.shared._record(db,item['id'])
        record.update(space_id=self.space,content=item['content']+'，已人工补充核对。')
        p=self.shared.preview(self.token('owner'),record)
        self.shared.confirm(self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True,'acknowledge_conflicts':True})

    def dump(self):
        with self.store.connect() as db:return '\n'.join(db.iterdump())

    def test_import_batch_and_original_exact_snapshot_without_preview_writes(self):
        batch,items=self.make()
        before=self.dump();result=self.call('list');detail=self.call('detail',batch_id=batch)
        source=self.call('source',batch_id=batch,id=items[0]['id'],expected_version=1,include_full=True)
        p=self.propose(batch)
        self.assertEqual(before,self.dump())
        self.assertEqual(result['batches'][0]['name'],'十一月活动纪要')
        self.assertEqual(detail['batch']['eligible_count'],2)
        self.assertEqual(source['source_text'],'周六十点在公园集合。\n\n甲负责带物资。')
        self.assertEqual(source['quotes'][0]['text'],items[0]['content'])
        p['withdraw'][0]['content']='客户端伪造';p['reason']='伪造'
        self.assertEqual(self.confirm(p,ids=['not-the-batch'])['withdrawn'],2)
        detail=self.call('detail',batch_id=batch)
        self.assertEqual(detail['batch']['trash_count'],2)
        self.assertEqual(detail['events'][0]['reason'],'导入了错误版本，先撤销后核对')
        self.assertEqual(self.call('source',batch_id=batch,id=items[0]['id'],expected_version=1,include_full=True)['source_text'],source['source_text'])

    def test_original_not_retained_without_consent_and_legacy_not_guessed(self):
        self.publish(content='早先手动添加的记忆')
        batch,items=self.make(keep=False)
        self.assertFalse(self.call('source',batch_id=batch,id=items[0]['id'],expected_version=1)['available'])
        self.assertEqual(items[0]['source_name'],'活动通知.txt')
        self.assertEqual(self.call('list')['untracked_count'],1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_evidence').fetchone()[0],0)

    def test_visibility_counts_quotes_full_source_and_batch_title_are_scoped(self):
        batch,items=self.make(visibility='selected',viewers=[self.uid('reader')])
        self.assertEqual(self.call('list','editor')['batches'],[])
        self.assertEqual(self.call('list','reader')['batches'][0]['name'],'共同资料导入批次')
        self.assertNotIn('十一月活动纪要',json.dumps(self.call('detail','reader',batch_id=batch),ensure_ascii=False))
        self.error(403,self.call,'detail','editor',batch_id=batch)
        result=self.call('source','reader',batch_id=batch,id=items[0]['id'],expected_version=1)
        self.assertNotIn('source_text',result)
        self.error(403,self.call,'source','reader',batch_id=batch,id=items[0]['id'],expected_version=1,include_full=True)
        self.error(403,self.propose,batch,'reader')
        self.error(403,self.call,'list','outsider')
        other=self.publish(content='不同批次之外的原文')
        self.error(403,self.call,'source',batch_id=batch,id=other['id'],expected_version=1)
        self.confirm(self.propose(batch))
        self.assertEqual(self.call('list','reader')['batches'],[])
        self.error(403,self.call,'source','reader',batch_id=batch,id=items[0]['id'],expected_version=1)

    def test_mixed_batch_retains_edits_replacements_and_prior_trash(self):
        batch,items=self.make(text='一号活动公园集合。\n\n二号活动北门集合。\n\n三号活动南门集合。\n\n四号活动图书馆集合。')
        self.edit(items[1])
        p=self.shared.replacements.preview(self.token('owner'),{'space_id':self.space,'id':items[2]['id'],'version':1,'content':'三号活动改为东门集合','reason':'已与负责人核实'})
        self.shared.replacements.confirm(self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True,'replacement_confirmed':True,'acknowledge_conflicts':True})
        self.shared.delete_memory(self.token('owner'),{'space_id':self.space,'id':items[3]['id'],'version':1,'confirmed':True})
        p=self.propose(batch)
        self.assertEqual(len(p['withdraw']),1);self.assertEqual(len(p['retained']),3)
        self.assertTrue(any('已经修改' in i['reason'] for i in p['retained']))
        self.assertTrue(any('新旧安排' in i['reason'] for i in p['retained']))
        result=self.confirm(p);self.assertEqual(result['withdrawn'],1)
        remaining=self.shared.memories(self.token('owner'),self.space)['memories']
        self.assertEqual(len(remaining),3)
        self.error(409,self.propose,batch)

    def test_unrelated_editor_reader_downgrade_and_new_sessions_cannot_confirm(self):
        batch,_=self.make('editor');p=self.propose(batch,'editor')
        self.error(403,self.confirm,p,'owner')
        other=self.shared.login({'name':'editor','password':'test-password-2026'})['token']
        self.error(403,self.shared.batches.confirm,other,{'space_id':self.space,'token':p['token'],'confirmed':True,'scope_confirmed':True})
        self.member('editor','reader')
        self.error(409,self.confirm,p,'editor')
        self.assertFalse(self.call('detail','editor',batch_id=batch)['batch']['can_undo'])

    def test_missing_double_confirmation_expiry_cancel_and_space_binding(self):
        batch,_=self.make();p=self.propose(batch)
        self.error(400,self.confirm,p,scope_confirmed=False)
        self.shared.batches.plans[p['token']]['expires']=0
        self.error(409,self.confirm,p)
        p=self.propose(batch);self.call('cancel',token=p['token']);self.error(409,self.confirm,p)
        p=self.propose(batch);other=self.shared.create_space(self.token('owner'),{'name':'另一空间','kind':'team'})['id']
        self.error(403,self.shared.batches.confirm,self.token('owner'),{'space_id':other,'token':p['token'],'confirmed':True,'scope_confirmed':True})
        self.assertEqual(self.call('detail',batch_id=batch)['batch']['active_count'],2)

    def test_preview_version_race_rejects_and_requires_new_preview(self):
        batch,items=self.make();p=self.propose(batch);self.edit(items[0])
        self.error(409,self.confirm,p)
        self.assertEqual(self.call('detail',batch_id=batch)['batch']['active_count'],2)
        p=self.propose(batch);self.assertEqual(len(p['withdraw']),1)
        self.confirm(p)

    def test_atomic_failure_rolls_back_trash_history_receipts_and_allows_retry(self):
        batch,_=self.make();p=self.propose(batch);before=self.dump()
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON shared_import_undos BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.confirm(p)
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_batch')
        self.assertEqual(before,self.dump())
        self.assertEqual(self.confirm(p)['withdrawn'],2)

    def test_receipt_survives_restart_and_duplicate_confirmation_does_not_remove_more(self):
        batch,_=self.make();p=self.propose(batch);result=self.confirm(p)
        self.shared.batches=SharedBatches(self.shared)
        self.assertTrue(self.confirm(p)['already_applied'])
        self.assertEqual(self.confirm(p)['withdrawn'],result['withdrawn'])
        self.assertEqual(self.call('detail',batch_id=batch)['batch']['trash_count'],2)

    def test_aggregate_trash_capacity_rejected_before_any_write(self):
        batch,items=self.make()
        with self.store.connect() as db:
            record=self.shared._record(db,items[0]['id']);snapshot=self.shared.recovery.encode(self.shared.recovery.snapshot(db,record))
            db.executemany('INSERT INTO shared_trash(id,space,memory,author,deleted_by,snapshot) VALUES (?,?,?,?,?,?)',
                           [('dummy'+str(i),self.space,'dummy'+str(i),self.uid('owner'),self.uid('owner'),snapshot) for i in range(99)])
        before=self.dump();self.error(400,self.propose,batch);self.assertEqual(before,self.dump())

    def test_retrieval_invalidation_and_recovery_keep_batch_identity(self):
        batch,items=self.make();self.shared.retriever=Mock()
        self.confirm(self.propose(batch))
        self.assertEqual(self.shared.retriever.invalidate.call_count,2)
        self.shared.retriever=None
        trash=self.shared.recovery.list(self.token('owner'),{'space_id':self.space})['items'][0]
        p=self.shared.recovery.preview(self.token('owner'),{'space_id':self.space,'operation':'trash_restore','trash_id':trash['trash_id'],'reason':'批次撤销后发现应保留'})
        self.shared.recovery.confirm(self.token('owner'),{'space_id':self.space,'token':p['token'],'confirmed':True,'scope_confirmed':True,'acknowledge_conflicts':True})
        detail=self.call('detail',batch_id=batch)
        self.assertEqual(detail['batch']['active_count'],1)
        restored=next(i for i in detail['items'] if i['state']=='active')
        self.assertFalse(restored['eligible']);self.assertEqual(restored['status'],'pending')
        self.assertEqual(self.call('source',batch_id=batch,id=restored['id'],expected_version=restored['version'],include_full=True)['source_name'],'活动通知.txt')
        sources=self.shared.question_context(self.token('reader'),self.space,'公园集合物资')['sources']
        self.assertTrue(all(i['id']==restored['id'] for i in sources))

    def test_legacy_backup_duplicate_signature_is_stable_across_upgrade(self):
        self.publish(content='没有批次的历史资料');service=SharedBackup(self.shared)
        document=service.export(self.token('owner'),{'space_id':self.space,'confirmed':True})['document']
        names={a['id']:a['name'] for a in document['accounts']}
        legacy={k:v for k,v in document.items() if k not in ('exported_at','batches')}
        with self.store.connect() as db:
            actor=self.shared._actor(db,self.token('owner'))
            accounts=service.resolve_accounts(db,actor,document,names)
        expected=service.digest({'document':legacy,'mapping':accounts,'name':'旧文件重复恢复'})
        p=service.preview(self.token('owner'),{'document':document,'name':'旧文件重复恢复','mapping':names})
        self.assertEqual(service.plans[p['token']]['signature'],expected)

    def test_backup_roundtrip_preserves_batches_and_accepts_old_format(self):
        batch,items=self.make('editor');self.edit(items[1]);service=SharedBackup(self.shared)
        document=service.export(self.token('owner'),{'space_id':self.space,'confirmed':True})['document']
        self.assertEqual(len(document['batches']),1)
        p=service.preview(self.token('owner'),{'document':document,'name':'批次备份恢复','mapping':{a['id']:a['name'] for a in document['accounts']}})
        target=service.confirm(self.token('owner'),{'token':p['token'],'confirmed':True,'permissions_confirmed':True})['space_id']
        batches=self.shared.batches.list(self.token('owner'),{'space_id':target})['batches']
        self.assertEqual(len(batches),1);self.assertNotEqual(batches[0]['id'],batch)
        detail=self.shared.batches.detail(self.token('owner'),{'space_id':target,'batch_id':batches[0]['id']})
        self.assertEqual(detail['batch']['eligible_count'],1)
        self.assertTrue(all(i['id'] not in {r['id'] for r in items} for i in detail['items']))
        old=copy.deepcopy(document);old.pop('batches');self.assertEqual(service.validate(old)['batches'],[])
        bad=copy.deepcopy(document);bad['batches'][0]['items'][0]['memory']='forged'
        self.error(400,service.validate,bad)
        bad=copy.deepcopy(document);bad['batches'][0]['items']*=2
        self.error(400,service.validate,bad)


class BatchHTTPTests(unittest.TestCase):
    from test_personal_http import PersonalHTTPTests as HTTPFixture
    setUpClass=classmethod(HTTPFixture.setUpClass.__func__)
    tearDownClass=classmethod(HTTPFixture.tearDownClass.__func__)
    from test_shared_http import SharedHTTPTests as HTTPHelper
    post=HTTPHelper.post

    def test_text_import_source_preview_cancel_confirm_and_idempotent_http(self):
        self.assertEqual(self.post('/batches/list',{'space_id':'x'})[0],401)
        self.post('/auth/register',{'name':'batch-http','password':'test-password-2026'})
        auth=self.post('/auth/login',{'name':'batch-http','password':'test-password-2026'})[1]['token']
        space=self.post('/spaces/create',{'name':'虚构文字批次测试','kind':'team'},auth)[1]['id']
        def post(action,**data):
            code,result=self.post(action,{'space_id':space,**data},auth);self.assertEqual(code,200,str(result));return result
        draft=post('/import/organize',sources=[{'name':'活动纪要','text':'乙负责接待。\n\n周日十一点图书馆集合。'}])
        plan=post('/import/preview',items=[{**c,'visibility':'all','edit_policy':'author'} for c in draft['candidates']],keep_evidence=True,batch_name='虚构活动纪要')
        batch=post('/import/confirm',token=plan['token'],confirmed=True)['batch_id']
        detail=post('/batches/detail',batch_id=batch);item=detail['items'][0]
        self.assertIn('乙负责接待',post('/batches/source',batch_id=batch,id=item['id'],expected_version=item['version'],include_full=True)['source_text'])
        p=post('/batches/preview',batch_id=batch,reason='测试取消');post('/batches/cancel',token=p['token'])
        self.assertEqual(self.post('/batches/confirm',{'space_id':space,'token':p['token'],'confirmed':True,'scope_confirmed':True},auth)[0],409)
        self.assertEqual(len(post('/memories/list')['memories']),2)
        p=post('/batches/preview',batch_id=batch,reason='测试整批移入回收站')
        self.assertEqual(self.post('/batches/confirm',{'space_id':space,'token':p['token'],'confirmed':True},auth)[0],400)
        result=post('/batches/confirm',token=p['token'],confirmed=True,scope_confirmed=True)
        self.assertEqual(result['withdrawn'],2)
        self.assertTrue(post('/batches/confirm',token=p['token'],confirmed=True,scope_confirmed=True)['already_applied'])
        self.assertEqual(post('/memories/list')['memories'],[])
        self.assertEqual(post('/ask',query='周日几点集合')['sources'],[])
        self.assertEqual(len(post('/recovery/list')['items']),2)
        self.assertEqual(self.post('/batches/preview',[],auth)[0],400)


if __name__=='__main__':unittest.main()
