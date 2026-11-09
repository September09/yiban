"""Space authentication, ACLs, review races and retrieval use isolated SQLite databases."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError


class SharedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=MemoryStore(Path(self.temp.name)/'test.sqlite3')
        self.shared=SharedMemory(self.store)
        self.users={}
        for name in ('owner','editor','reader','outsider'):
            self.shared.register({'name':name,'password':'test-password-2026'})
            self.users[name]=self.shared.login({'name':name,'password':'test-password-2026'})
        self.space=self.shared.create_space(self.token('owner'),{'name':'测试家庭','kind':'family'})['id']
        for name,role in [('editor','editor'),('reader','reader')]:self.member(name,role)

    def tearDown(self):self.temp.cleanup()
    def token(self,name):return self.users[name]['token']
    def uid(self,name):return self.users[name]['account']['id']
    def member(self,name,role):
        return self.shared.set_member(self.token('owner'),{'space_id':self.space,'name':name,'role':role})
    def fail(self,status,fn,*args):
        with self.assertRaises(SharedError) as result:fn(*args)
        self.assertEqual(result.exception.status,status)
    def payload(self,**changes):
        data={'space_id':self.space,'content':'家庭周末一起散步','visibility':'all','edit_policy':'author','viewers':[]}
        data.update(changes);return data
    def publish(self,name='owner',**changes):
        plan=self.shared.preview(self.token(name),self.payload(**changes))
        result=self.shared.confirm(self.token(name),{'space_id':self.space,'token':plan['token'],'confirmed':True,'acknowledge_conflicts':True})
        return next(x for x in self.shared.memories(self.token(name),self.space)['memories'] if x['id']==result['id'])
    def edit_payload(self,item,**changes):
        return self.payload(**{k:item[k] for k in ('id','version','content','visibility','edit_policy','viewers','event_date','source_note')},**changes)

    def test_authenticated_actor_cannot_be_forged(self):
        for token in (None,'bad',self.token('owner')+'x'):
            self.fail(401,self.shared.memories,token,self.space)
        self.fail(403,self.shared.set_member,self.token('reader'),{'space_id':self.space,'name':'outsider','role':'editor','account_id':self.uid('owner')})
        item=self.publish('editor',author=self.uid('owner'),profile_id=self.uid('owner'))
        self.assertEqual(item['author'],self.uid('editor'))
        self.fail(403,self.shared.preview,self.token('reader'),self.payload(author=self.uid('owner')))
        self.assertEqual(self.shared.spaces(self.token('outsider'))['spaces'],[])

    def test_hashes_logout_expiry_password_and_throttle(self):
        with self.store.connect() as db:
            raw=' '.join(str(tuple(row)) for row in db.execute('SELECT * FROM shared_accounts'))
            self.assertNotIn('test-password-2026',raw)
            self.assertNotIn(self.token('owner'),str([tuple(x) for x in db.execute('SELECT * FROM shared_sessions')]))
        self.shared.logout(self.token('editor'));self.fail(401,self.shared.me,self.token('editor'))
        with self.store.connect() as db:db.execute('UPDATE shared_sessions SET expires=0 WHERE account=?',(self.uid('reader'),))
        self.fail(401,self.shared.me,self.token('reader'))
        second=self.shared.login({'name':'OWNER','password':'test-password-2026'})['token']
        self.shared.change_password(self.token('owner'),{'old_password':'test-password-2026','new_password':'changed-test-password'})
        for token in (second,self.token('owner')):self.fail(401,self.shared.me,token)
        self.shared.login({'name':'owner','password':'changed-test-password'})
        for _ in range(5):self.fail(401,self.shared.login,{'name':'outsider','password':'wrong-password'})
        self.fail(429,self.shared.login,{'name':'outsider','password':'wrong-password'})

    def test_selected_visibility_and_history_do_not_leak(self):
        item=self.publish('editor',content='只给作者和管理员的记录',visibility='selected')
        self.assertEqual(len(self.shared.memories(self.token('owner'),self.space)['memories']),1)
        self.assertEqual(self.shared.memories(self.token('reader'),self.space)['memories'],[])
        for name in ('reader','outsider'):
            self.fail(403,self.shared.history,self.token(name),{'space_id':self.space,'id':item['id']})
            self.fail(403,self.shared.preview,self.token(name),self.edit_payload(item))
        self.assertEqual(self.shared.history(self.token('editor'),{'space_id':self.space,'id':item['id']})['total'],1)
        for name in ('owner','editor'):
            self.assertEqual(len(self.shared.question_context(self.token(name),self.space,'记录')['sources']),1)
        self.assertEqual(self.shared.question_context(self.token('reader'),self.space,'记录')['sources'],[])

    def test_reader_can_view_but_not_mutate_or_see_history(self):
        item=self.publish(visibility='selected',viewers=[self.uid('reader')])
        actual=self.shared.memories(self.token('reader'),self.space)['memories'][0]
        for key in ('can_edit','can_delete','can_configure','can_history'):self.assertFalse(actual[key])
        self.fail(403,self.shared.preview,self.token('reader'),self.edit_payload(item))
        self.fail(403,self.shared.delete_memory,self.token('reader'),{'space_id':self.space,'id':item['id'],'version':item['version'],'confirmed':True})
        self.fail(403,self.shared.history,self.token('reader'),{'space_id':self.space,'id':item['id']})

    def test_editor_policy_allows_body_only_and_preserves_author(self):
        item=self.publish(edit_policy='editors',visibility='selected',viewers=[self.uid('editor'),self.uid('owner')])
        self.assertEqual(item['viewers'],[self.uid('editor')])
        payload=self.edit_payload(item);payload['content']='编辑成员补充的正文'
        plan=self.shared.preview(self.token('editor'),payload)
        self.shared.confirm(self.token('editor'),{'space_id':self.space,'token':plan['token'],'confirmed':True})
        result=self.shared.memories(self.token('owner'),self.space)['memories'][0]
        self.assertEqual(result['author'],self.uid('owner'));self.assertEqual(result['editor'],self.uid('editor'))
        for field,value in [('visibility','all'),('edit_policy','author'),('viewers',[self.uid('reader')])]:
            altered=self.edit_payload(result);altered[field]=value
            self.fail(403,self.shared.preview,self.token('editor'),altered)
        self.fail(403,self.shared.delete_memory,self.token('editor'),{'space_id':self.space,'id':item['id'],'version':result['version'],'confirmed':True})
        self.fail(403,self.shared.history,self.token('editor'),{'space_id':self.space,'id':item['id']})
        history=self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})
        self.assertEqual(history['total'],2);self.assertEqual(history['entries'][0]['actor_name'],'editor')
        self.assertEqual(history['entries'][0]['before']['content'],item['content'])
        protected=self.publish(content='作者专有编辑内容')
        self.fail(403,self.shared.preview,self.token('editor'),self.edit_payload(protected))

    def test_invisible_editor_cannot_edit_even_with_editors_policy(self):
        item=self.publish(visibility='selected',edit_policy='editors')
        self.fail(403,self.shared.preview,self.token('editor'),self.edit_payload(item))

    def test_removed_member_and_old_plan_cannot_access(self):
        item=self.publish('editor',visibility='selected')
        plan=self.shared.preview(self.token('editor'),self.payload())
        context=self.shared.question_context(self.token('editor'),self.space,'家庭')
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('editor')},True)
        self.fail(403,self.shared.memories,self.token('editor'),self.space)
        self.fail(403,self.shared.history,self.token('editor'),{'space_id':self.space,'id':item['id']})
        self.fail(403,self.shared.confirm,self.token('editor'),{'space_id':self.space,'token':plan['token'],'confirmed':True})
        self.fail(403,self.shared.validate_answer,self.token('editor'),context['guard'])
        self.assertEqual(self.shared.spaces(self.token('editor'))['spaces'],[])

    def test_downgraded_author_cannot_edit_delete_or_publish(self):
        item=self.publish('editor')
        plan=self.shared.preview(self.token('editor'),self.payload())
        self.member('editor','reader')
        listed=self.shared.memories(self.token('editor'),self.space)['memories'][0]
        self.assertFalse(listed['can_edit']);self.assertFalse(listed['can_delete'])
        self.fail(403,self.shared.preview,self.token('editor'),self.edit_payload(item))
        self.fail(403,self.shared.confirm,self.token('editor'),{'space_id':self.space,'token':plan['token'],'confirmed':True})
        self.fail(403,self.shared.delete_memory,self.token('editor'),{'space_id':self.space,'id':item['id'],'version':1,'confirmed':True})

    def test_readding_does_not_restore_selected_grants(self):
        self.publish(visibility='selected',viewers=[self.uid('reader')])
        self.shared.set_member(self.token('owner'),{'space_id':self.space,'account_id':self.uid('reader')},True)
        self.member('reader','reader')
        self.assertEqual(self.shared.memories(self.token('reader'),self.space)['memories'],[])

    def test_preview_confirmation_identity_version_and_single_use(self):
        plan=self.shared.preview(self.token('owner'),self.payload())
        data={'space_id':self.space,'token':plan['token'],'confirmed':True}
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])
        self.fail(400,self.shared.confirm,self.token('owner'),dict(data,confirmed=False))
        self.fail(403,self.shared.confirm,self.token('editor'),data)
        self.fail(403,self.shared.confirm,self.token('owner'),dict(data,space_id='other'))
        self.shared.confirm(self.token('owner'),dict(data,content='伪造确认内容'))
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'][0]['content'],'家庭周末一起散步')
        self.fail(409,self.shared.confirm,self.token('owner'),data)
        plan=self.shared.preview(self.token('owner'),self.payload())
        self.member('reader','editor')
        self.fail(409,self.shared.confirm,self.token('owner'),dict(data,token=plan['token']))
        plan=self.shared.preview(self.token('owner'),self.payload())
        self.shared._plans[plan['token']]['expires']=time.monotonic()-1
        self.fail(409,self.shared.confirm,self.token('owner'),dict(data,token=plan['token']))

    def test_conflicts_only_visible_and_need_ack(self):
        self.publish(content='我喜欢咖啡',visibility='selected')
        hidden=self.shared.preview(self.token('editor'),self.payload(content='我不喜欢咖啡'))
        self.assertEqual(hidden['conflicts'],[])
        visible=self.shared.preview(self.token('owner'),self.payload(content='我不喜欢咖啡'))
        self.assertEqual(len(visible['conflicts']),1)
        data={'space_id':self.space,'token':visible['token'],'confirmed':True}
        self.fail(400,self.shared.confirm,self.token('owner'),data)
        self.shared.confirm(self.token('owner'),dict(data,acknowledge_conflicts=True))
        self.assertEqual(len(self.shared.memories(self.token('owner'),self.space)['memories']),2)

    def test_history_and_body_rollback_together(self):
        plan=self.shared.preview(self.token('owner'),self.payload())
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_shared_history BEFORE INSERT ON shared_history BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.shared.confirm(self.token('owner'),{'space_id':self.space,'token':plan['token'],'confirmed':True})
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_audience').fetchone()[0],0)

    def test_space_isolation_and_personal_data_preserved_on_delete(self):
        profile=self.store.create_profile('私人档案')['id'];self.store.save(profile,'私人内容');self.store.enroll(profile,[1,0,0],'voice')
        other=self.shared.create_space(self.token('owner'),{'name':'另一团队','kind':'team'})['id']
        item=self.publish()
        self.fail(403,self.shared.history,self.token('owner'),{'space_id':other,'id':item['id']})
        self.assertEqual(self.shared.memories(self.token('owner'),other)['memories'],[])
        context=self.shared.question_context(self.token('owner'),self.space,'私人内容')
        self.assertNotIn('私人内容',context['context'])
        version=self.shared.memories(self.token('owner'),self.space)['space']['version']
        self.fail(409,self.shared.delete_space,self.token('owner'),{'space_id':self.space,'version':version-1,'confirmed':True})
        self.shared.delete_space(self.token('owner'),{'space_id':self.space,'version':version,'confirmed':True})
        with self.store.connect() as db:
            for table in ('shared_memories','shared_history','shared_audience'):self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM voiceprints').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.store.memories(profile)[0]['content'],'私人内容')

    def test_question_context_filters_and_revalidates(self):
        self.publish(content='可以公开给成员的野餐地点',edit_policy='editors')
        self.publish(content='不可见的秘密',visibility='selected')
        context=self.shared.question_context(self.token('reader'),self.space,'野餐地点')
        self.assertEqual(len(context['sources']),1);self.assertNotIn('不可见的秘密',context['context'])
        self.shared.validate_answer(self.token('reader'),context['guard'])
        self.member('reader','editor')
        self.fail(409,self.shared.validate_answer,self.token('reader'),context['guard'])

    def test_invalid_viewer_date_roles_and_size_rejected(self):
        for changes in ({'viewers':[self.uid('outsider')],'visibility':'selected'}, {'event_date':'2026-02-30'}, {'content':'a'*1201}, {'edit_policy':'everyone'}):
            with self.assertRaises(SharedError):self.shared.preview(self.token('owner'),self.payload(**changes))
        self.fail(400,self.shared.set_member,self.token('owner'),{'space_id':self.space,'name':'editor','role':'owner'})
        self.fail(400,self.shared.set_member,self.token('owner'),{'space_id':self.space,'account_id':self.uid('owner')},True)


if __name__=='__main__':unittest.main()
