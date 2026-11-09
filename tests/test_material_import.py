"""Synthetic materials and isolated databases; never import into the user's space."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_shared_memory as fixture
from yiban.memory.shared_import import SharedImport
from yiban.memory.shared_memory import SharedError
from yiban.backends.models import ModelClient
from yiban.memory.material_reader import read_file
from yiban.turns import Cancellation, Cancelled

class MaterialTests(unittest.TestCase):
    setUp=fixture.SharedMemoryTests.setUp
    tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token
    uid=fixture.SharedMemoryTests.uid
    member=fixture.SharedMemoryTests.member
    def fail(self,status,fn,*args,**kwargs):
        with self.assertRaises(SharedError) as result:fn(*args,**kwargs)
        self.assertEqual(result.exception.status,status)
    payload=fixture.SharedMemoryTests.payload
    publish=fixture.SharedMemoryTests.publish

    def setUp(self):
        fixture.SharedMemoryTests.setUp(self)
        self.material=SharedImport(self.shared,ModelClient({'model':'test','base_url':'http://127.0.0.1:11434','max_tokens':100}))
    def batch(self,*contents,**changes):
        return {'space_id':self.space,'items':[self.payload(content=c,**changes) for c in contents]}
    def confirm(self,plan,**changes):
        return self.material.confirm(self.token('editor'),dict(space_id=self.space,token=plan['token'],confirmed=True,**changes))
    def count(self):
        with self.store.connect() as db:return db.execute('SELECT COUNT(*) FROM shared_memories').fetchone()[0]
    def organize(self,**changes):
        data={'space_id':self.space,'sources':[{'name':'活动.txt','text':'周五活动在公园。\n\n负责人是甲。'}]}
        data.update(changes)
        return self.material.organize(self.token('editor'),data)
    def read(self,name,raw,who='editor'):
        return self.material.read(self.token(who),{'space_id':self.space,'name':name,'base64':base64.b64encode(raw).decode()})

    def test_preview_no_writes_frozen_atomic_history_acl_and_replay(self):
        data=self.batch('周五去公园','甲负责物资',visibility='selected',viewers=[self.uid('reader')])
        plan=self.material.preview(self.token('editor'),data)
        self.assertEqual(self.count(),0)
        data['items'][0]['content']='tampered'
        plan['items'][0]['payload']['content']='client tampered'
        self.assertEqual(self.confirm(plan)['imported'],2)
        items=self.shared.memories(self.token('reader'),self.space)['memories']
        self.assertEqual({x['content'] for x in items},{'周五去公园','甲负责物资'})
        self.assertTrue(all(x['author']==self.uid('editor') for x in items))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_history').fetchone()[0],2)
        self.fail(409,self.confirm,plan)

    def test_no_confirmation_no_write_and_session_binding(self):
        p=self.material.preview(self.token('editor'),self.batch('活动资料'))
        self.fail(400,self.material.confirm,self.token('editor'),{'space_id':self.space,'token':p['token']})
        new=self.shared.login({'name':'editor','password':'test-password-2026'})['token']
        self.fail(403,self.material.confirm,new,{'space_id':self.space,'token':p['token'],'confirmed':True})
        self.assertEqual(self.count(),0)

    def test_permission_version_expiry_discard(self):
        self.fail(403,self.material.preview,self.token('reader'),self.batch('不能导入'))
        self.fail(403,self.material.preview,self.token('outsider'),self.batch('不能导入'))
        p=self.material.preview(self.token('editor'),self.batch('预览'))
        self.member('reader','editor')
        self.fail(409,self.confirm,p)
        p=self.material.preview(self.token('editor'),self.batch('过期'))
        self.material.plans[p['token']]['expires']=time.monotonic()-1
        self.fail(409,self.confirm,p)
        p=self.material.preview(self.token('editor'),self.batch('取消'))
        self.material.discard(self.token('editor'),{'space_id':self.space,'token':p['token']})
        self.fail(409,self.confirm,p)
        p=self.material.preview(self.token('editor'),self.batch('降级'))
        self.member('editor','reader')
        self.fail(403,self.confirm,p)
        self.assertEqual(self.count(),0)

    def test_duplicate_only_visible_and_batch_conflicts(self):
        self.publish(content='隐私事实',visibility='selected')
        self.publish(content='公开事实')
        p=self.material.preview(self.token('editor'),self.batch('隐私事实','公开事实',' 新内容 ','新内容'))
        self.assertEqual(p['skipped'],2)
        self.assertEqual(len(p['items']),2)
        p=self.material.preview(self.token('editor'),self.batch('我的居住城市：北京','我的居住城市：上海'))
        self.assertTrue(p['conflicts'])
        self.fail(400,self.confirm,p)
        self.assertEqual(self.confirm(p,acknowledge_conflicts=True)['imported'],2)

    def test_failed_batch_rolls_back_every_row(self):
        p=self.material.preview(self.token('editor'),self.batch('one','two'))
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_test BEFORE INSERT ON shared_memories WHEN NEW.content='two' BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaises(Exception):self.confirm(p)
        self.assertEqual(self.count(),0)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shared_history').fetchone()[0],0)

    def test_paragraphs_preserve_text_limit_and_unknown_date(self):
        result=self.organize()
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(x['event_date']=='' for x in result['candidates']))
        self.assertEqual(self.count(),0)
        long='长'*2200
        result=self.organize(sources=[{'name':'x','text':long}])
        self.assertEqual(''.join(x['content'] for x in result['candidates']),long)
        self.fail(400,self.organize,sources=[{'name':'x','text':'长'*30001}])

    def test_ai_grounding_failure_and_remote_consent(self):
        good=json.dumps([{'content':'活动在公园','source_ids':[1]}],ensure_ascii=False)
        def events(*args,**kwargs):
            yield {'message':{'content':good}}
            yield {'done':True}
        with patch('yiban.memory.shared_import.ModelClient.chat',events):
            result=self.organize(mode='ai')
            self.assertEqual(result['candidates'][0]['content'],'活动在公园')
        good='[{"content":"invented","source_ids":[999]}]'
        with patch('yiban.memory.shared_import.ModelClient.chat',events):self.fail(400,self.organize,mode='ai')
        self.material.models.config['base_url']='https://cloud.example'
        with patch('yiban.memory.shared_import.ModelClient.chat') as model:
            self.fail(400,self.organize,mode='ai')
            model.assert_not_called()
        self.assertEqual(self.count(),0)

    def test_cancellation_and_permission_change_during_generation(self):
        cancellation=Cancellation();cancellation.cancel()
        with self.assertRaises(Cancelled):
            self.material.organize(self.token('editor'),{'space_id':self.space,'sources':[{'name':'x','text':'x'}]},cancellation)
        def events(*args,**kwargs):
            self.member('editor','reader')
            yield {'message':{'content':'[]'},'done':True}
        with patch('yiban.memory.shared_import.ModelClient.chat',events):self.fail(403,self.organize,mode='ai')

    def test_file_read_text_json_csv_docx(self):
        self.assertEqual(self.read('chat.txt','甲：周五去公园。'.encode())['text'],'甲：周五去公园。')
        self.assertIn('甲',self.read('chat.txt','甲'.encode('gb18030'))['text'])
        self.assertIn('甲',self.read('chat.json','[{"人":"甲"}]'.encode())['text'])
        self.assertIn('甲 | 周五',self.read('chat.csv','姓名,时间\n甲,周五'.encode())['text'])
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>活动资料</w:t></w:r></w:p></w:body></w:document>')
        self.assertIn('活动资料',self.read('activity.docx',raw.getvalue())['text'])
        self.fail(403,self.read,'private.txt',b'text','reader')
        self.fail(400,self.read,'bad.exe',b'text')
        self.fail(400,self.read,'broken.pdf',b'not PDF')
        self.fail(400,self.read,'big.txt',b'x'*4_000_001)
        self.assertEqual(self.count(),0)


    def test_ollama_schema_is_opt_in(self):
        client=ModelClient({'model':'test','vision_model':'','backend':'ollama','max_tokens':100,'temperature':0.1})
        schema={'type':'array'}
        for requested in (None,schema):
            with patch.object(client,'stream_request',return_value=io.BytesIO(b'{"done":true}\n')) as request:
                list(client.chat([{'role':'user','content':'synthetic'}],json_schema=requested))
                payload=request.call_args.args[1]
                self.assertEqual(payload.get('format'),requested)
                if requested is None:self.assertNotIn('format',payload)

    def test_truncated_ai_response_and_capacity_rejected(self):
        def events(*args,**kwargs):yield {'message':{'content':'[]'},'done':True,'done_reason':'length'}
        with patch('yiban.memory.shared_import.ModelClient.chat',events):self.fail(400,self.organize,mode='ai')
        self.fail(400,self.material.preview,self.token('editor'),self.batch(*[str(i) for i in range(41)]))
        self.assertEqual(self.count(),0)

class MaterialHTTPTests(unittest.TestCase):
    from test_personal_http import PersonalHTTPTests as HTTPFixture
    setUpClass=classmethod(HTTPFixture.setUpClass.__func__)
    tearDownClass=classmethod(HTTPFixture.tearDownClass.__func__)
    from test_shared_http import SharedHTTPTests as HTTPHelper
    post=HTTPHelper.post
    def test_complete_http_import_and_auth(self):
        self.assertEqual(self.post('/import/capabilities',{'space_id':'x'})[0],401)
        self.post('/auth/register',{'name':'material-http','password':'test-password-2026'})
        token=self.post('/auth/login',{'name':'material-http','password':'test-password-2026'})[1]['token']
        space=self.post('/spaces/create',{'name':'资料测试','kind':'team'},token)[1]['id']
        code,result=self.post('/import/read',{'space_id':space,'name':'test.txt','base64':base64.b64encode('活动在公园。'.encode()).decode()},token)
        self.assertEqual(code,200)
        code,result=self.post('/import/organize',{'space_id':space,'sources':[result]},token)
        self.assertEqual(code,200)
        item=dict(result['candidates'][0],visibility='all',edit_policy='author',source_note='测试文件')
        code,plan=self.post('/import/preview',{'space_id':space,'items':[item],'keep_evidence':True},token)
        self.assertEqual(code,200)
        self.assertEqual(self.post('/memories/list',{'space_id':space},token)[1]['memories'],[])
        self.assertEqual(self.post('/import/confirm',{'space_id':space,'token':plan['token'],'confirmed':True},token)[0],200)
        memories=self.post('/memories/list',{'space_id':space},token)[1]['memories']
        self.assertEqual(len(memories),1)
        self.assertEqual(memories[0]['source_note'],'测试文件')
        memory=memories[0]
        self.assertEqual(memory['status'],'pending');self.assertTrue(memory['has_evidence'])
        code,evidence=self.post('/memories/evidence',{'space_id':space,'id':memory['id']},token)
        self.assertEqual(code,200);self.assertNotIn('source_text',evidence)
        self.assertEqual(self.post('/memories/evidence',{'space_id':space,'id':memory['id']})[0],401)
        self.assertIn('待核实',self.post('/ask',{'space_id':space,'query':'活动'},token)[1]['answer'])
        edited=dict(memory,space_id=space,status='outdated',status_reason='活动安排已变更')
        code,plan=self.post('/memories/preview',edited,token);self.assertEqual(code,200)
        self.assertEqual(self.post('/memories/confirm',{'space_id':space,'token':plan['token'],'confirmed':True},token)[0],200)
        self.assertEqual(self.post('/ask',{'space_id':space,'query':'活动'},token)[1]['sources'],[])
        answer=self.post('/ask',{'space_id':space,'query':'活动','include_outdated':True},token)[1]
        self.assertIn('过时',answer['answer']);self.assertEqual(len(answer['sources']),1)

if __name__=='__main__':unittest.main()
