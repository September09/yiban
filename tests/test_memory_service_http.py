"""One interactive memory service: aliases, scope, citations and archive isolation."""
import json
import urllib.request
import urllib.error
import unittest
import test_personal_http as fixture
from test_memory_v2 import packet

class UnifiedHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)
    def post(self,path,data,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request('http://127.0.0.1:'+str(self.http.server_port)+path,data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
    def actor(self,name):
        self.app.SHARED.register({'name':name,'password':'unified-fixture-password'})
        return self.app.SHARED.login({'name':name,'password':'unified-fixture-password'})['token']
    def publish(self,token,scope):
        status,p=self.post('/api/memories/preview',{**scope,'document':packet(),'visibility':'all'},token)
        self.assertEqual(status,200)
        self.assertEqual(self.post('/api/memories/confirm',{**scope,'token':p['token'],'confirmed':True,'acknowledge_warnings':True},token)[0],200)
    def test_every_question_alias_uses_current_records_even_version_one(self):
        owner=self.actor('unified-owner')
        space=self.app.SHARED.create_space(owner,{'name':'统一测试团队','kind':'team'})['id']
        self.publish(owner,{'scope_kind':'team','space_id':space})
        before=self.app.STRUCTURED_MEMORY.listing(owner,{'scope_kind':'team','space_id':space})['records']
        for path in ['/api/memories/ask','/api/companion/ask','/api/shared/ask','/api/shared/agent/ask','/api/shared/agent/memory/ask']:
            status,r=self.post(path,{'space_id':space,'scope_kind':'team','query':'第二届班长是谁','memory_version':1},owner)
            self.assertEqual(status,200,(path,r));self.assertIn('角色乙',r['answer'])
            self.assertTrue(r['answer_source_ids']);self.assertEqual(r['memory_version'],2)
        self.assertEqual(before,self.app.STRUCTURED_MEMORY.listing(owner,{'scope_kind':'team','space_id':space})['records'])
    def test_personal_login_and_no_team_are_sufficient(self):
        owner=self.actor('unified-personal')
        self.publish(owner,{'scope_kind':'personal'})
        status,r=self.post('/api/companion/ask',{'query':'第二届班长是谁'},owner)
        self.assertEqual(status,200);self.assertIn('角色乙',r['answer'])
        self.assertTrue(all(row['scope_kind']=='personal' for row in r['sources']))
        self.assertEqual(self.post('/api/memories/ask',{'query':'第二届班长是谁'})[0],401)
    def test_profile_notes_cannot_be_read_or_written_through_old_urls(self):
        profile=self.app.STORE.create_profile('演练档案')['id']
        for path in ['/api/memory/list','/api/memory/ask','/api/memory/save']:
            status,r=self.post(path,{'profile_id':profile,'query':'我喜欢什么','content':'不应保存'})
            self.assertEqual(status,410,(path,r))
        self.assertEqual(self.app.STORE.memories(profile),[])
    def test_retired_writes_cannot_create_a_second_library(self):
        owner=self.actor('unified-retired-write')
        for path in ['/api/shared/memories/preview','/api/shared/import/start','/api/shared/voice/start','/api/shared/agent/memory/confirm']:
            self.assertEqual(self.post(path,{'content':'不写入'},owner)[0],410,path)
    def test_ordinary_chat_does_not_write_or_inject_profile_notes(self):
        profile=self.app.STORE.create_profile('普通聊天档案')['id']
        self.app.STORE.request_save(profile,'演练秘密偏好')
        req=urllib.request.Request('http://127.0.0.1:'+str(self.http.server_port)+'/api/chat',
            data=json.dumps({'profile_id':profile,'messages':[{'role':'user','content':'记住：新的偏好'}]}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as r:
            chunks=[json.loads(x) for x in r.read().splitlines()]
        self.assertFalse(chunks[0]['memory_changed']);self.assertIn('记忆库',chunks[0]['message']['content'])
        self.assertEqual(len(self.app.STORE.memories(profile)),1)
    def test_read_only_member_and_original_source_permissions_are_preserved(self):
        owner=self.actor('unified-source-owner');reader=self.actor('unified-source-reader')
        space=self.app.SHARED.create_space(owner,{'name':'原文权限测试','kind':'team'})['id']
        self.app.SHARED.set_member(owner,{'space_id':space,'name':'unified-source-reader','role':'reader'})
        scope={'scope_kind':'team','space_id':space};self.publish(owner,scope)
        _,r=self.post('/api/memories/ask',{**scope,'query':'第二届班长是谁'},reader)
        row=next(x for x in r['sources'] if x['id'] in r['answer_source_ids'])
        status,e=self.post('/api/memories/evidence',{**scope,'id':row['id'],'expected_version':row['version']},reader)
        self.assertEqual(status,200);self.assertTrue(e['quotes'])
        self.assertEqual(self.post('/api/memories/source',{**scope,'id':row['id']},reader)[0],403)
        self.assertEqual(self.post('/api/memories/update/preview',{**scope,'id':row['id'],'expected_version':row['version'],'changes':{'summary':'不允许'}},reader)[0],403)
        status,e=self.post('/api/shared/memories/evidence',{'space_id':space,'id':row['id'],'expected_version':row['version']},reader)
        self.assertEqual(status,200);self.assertFalse(e['can_read_full'])
        self.assertEqual(self.post('/api/shared/memories/evidence',{'space_id':space,'id':row['id'],'include_full':True},reader)[0],403)

if __name__=='__main__':unittest.main()
