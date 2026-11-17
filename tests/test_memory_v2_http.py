"""Actual authenticated HTTP boundaries for structured memory v2, with synthetic sources."""
import json
import urllib.request
import urllib.error
import unittest
import test_personal_http as fixture
from test_memory_v2 import packet


class V2HTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PersonalHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PersonalHTTPTests.tearDownClass.__func__)

    def post(self,path,data,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())

    def test_auth_import_companion_count_and_source_endpoints(self):
        scope={'scope_kind':'personal'}
        self.assertEqual(self.post('/api/memory/v2/list',scope)[0],401)
        self.app.SHARED.register({'name':'v2-http-account','password':'v2-test-password'})
        token=self.app.SHARED.login({'name':'v2-http-account','password':'v2-test-password'})['token']
        status,plan=self.post('/api/memory/v2/preview',{**scope,'document':packet()},token);self.assertEqual(status,200)
        self.assertEqual(self.post('/api/memory/v2/list',scope,token)[1]['records'],[])
        status,saved=self.post('/api/memory/v2/confirm',{**scope,'token':plan['token'],'confirmed':True},token)
        self.assertEqual(status,200);self.assertEqual(saved['imported'],9)
        status,result=self.post('/api/companion/ask',{'memory_version':2,'query':'我个人记忆中的聚餐几次'},token)
        self.assertEqual(status,200);self.assertEqual(result['memory_version'],2);self.assertEqual(result['calculation']['groups'][0]['count'],2)
        source=result['sources'][0]
        self.assertEqual(self.post('/api/memory/v2/evidence',{**scope,'id':source['id'],'expected_version':1},token)[0],200)
        self.assertEqual(self.post('/api/memory/v2/source',{**scope,'id':source['id']},token)[0],200)
        self.app.SHARED.logout(token)
        self.assertEqual(self.post('/api/memory/v2/list',scope,token)[0],401)

    def test_companion_complex_query_uses_read_only_v2_tools(self):
        from unittest.mock import patch
        self.app.SHARED.register({'name':'v2-http-tools','password':'v2-test-password'})
        token=self.app.SHARED.login({'name':'v2-http-tools','password':'v2-test-password'})['token']
        scope={'scope_kind':'personal'}
        _,plan=self.post('/api/memory/v2/preview',{**scope,'document':packet(),'visibility':'all'},token)
        self.assertEqual(self.post('/api/memory/v2/confirm',{**scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True},token)[0],200)
        before=self.post('/api/memory/v2/list',scope,token)[1]['records']
        class Model:
            def chat(inner,messages,**kw):
                self.assertIn('calls',kw['json_schema']['properties'])
                yield {'message':{'content':json.dumps({'calls':[
                    {'tool':'query_facts','query':'第一届班长是谁'},
                    {'tool':'query_facts','query':'第二届班长是谁'}]},ensure_ascii=False)},'done':True}
        with patch.object(self.app.STRUCTURED_MEMORY,'models',Model()):
            status,result=self.post('/api/companion/ask',{**scope,'memory_version':2,'query':'第一届和第二届班长分别是谁'},token)
        self.assertEqual(status,200)
        self.assertEqual(result['agent']['mode'],'read_only_v2')
        self.assertEqual(result['agent']['status'],'done')
        self.assertIn('角色甲',result['answer']);self.assertIn('角色乙',result['answer'])
        self.assertTrue(result['answer_source_ids'])
        self.assertEqual(before,self.post('/api/memory/v2/list',scope,token)[1]['records'])

    def test_manual_note_draft_preview_and_confirmation(self):
        self.app.SHARED.register({'name':'v2-http-note','password':'v2-test-password'})
        token=self.app.SHARED.login({'name':'v2-http-note','password':'v2-test-password'})['token']
        scope={'scope_kind':'personal'}
        status,draft=self.post('/api/memory/v2/draft',{**scope,'title':'偏好','content':'我喜欢茉莉茶。'},token)
        self.assertEqual(status,200)
        self.assertEqual(self.post('/api/memory/v2/list',scope,token)[1]['records'],[])
        self.assertEqual(draft['document']['records'][0]['verification'],'pending')
        status,plan=self.post('/api/memory/v2/preview',{**scope,'document':draft['document']},token)
        self.assertEqual(status,200)
        self.assertEqual(self.post('/api/memory/v2/list',scope,token)[1]['records'],[])
        self.assertEqual(self.post('/api/memory/v2/confirm',{**scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True},token)[0],200)
        rows=self.post('/api/memory/v2/list',scope,token)[1]['records']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['verification'],'pending')
        row=rows[0]
        status,evidence=self.post('/api/memory/v2/evidence',{**scope,'id':row['id'],'expected_version':row['version']},token)
        self.assertEqual(status,200);self.assertEqual(evidence['quotes'][0]['text'],'我喜欢茉莉茶。')
        self.assertEqual(self.post('/api/memory/v2/draft',{**scope,'content':'未登录数据'})[0],401)


    def test_md_routes_prepare_redacted_candidates_without_writing(self):
        from unittest.mock import patch
        from test_md_extraction import LocalExtractor
        scope={'scope_kind':'personal'}
        self.assertEqual(self.post('/api/memory/v2/md/start',{**scope,'name':'x.md','content':'秘密'})[0],401)
        self.app.SHARED.register({'name':'md-http','password':'md-http-password'})
        token=self.app.SHARED.login({'name':'md-http','password':'md-http-password'})['token']
        with patch.object(self.app.STRUCTURED_MEMORY,'models',LocalExtractor()):
            status,draft=self.post('/api/memory/v2/md/start',{**scope,'name':'a.md','content':'柳叶完成志愿活动。\n\n联系电话13812345678'},token)
            self.assertEqual(status,200)
            data={**scope,'extraction_token':draft['extraction_token']}
            self.assertEqual(self.post('/api/memory/v2/md/next',data,token)[0],200)
            status,result=self.post('/api/memory/v2/md/review',data,token)
            self.assertEqual(status,200);self.assertEqual(result['private_count'],1)
            self.assertNotIn('13812345678',json.dumps(result['document']))
            self.assertEqual(self.post('/api/memory/v2/list',scope,token)[1]['records'],[])
            self.assertEqual(self.post('/api/memory/v2/md/discard',data,token)[0],200)
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/md-import.js')
        with urllib.request.urlopen(request) as r:self.assertEqual(r.status,200)

    def test_malformed_requests_fail_closed(self):
        self.app.SHARED.register({'name':'v2-http-invalid','password':'v2-test-password'})
        token=self.app.SHARED.login({'name':'v2-http-invalid','password':'v2-test-password'})['token']
        for path,data in [('preview',{'document':{}}),('confirm',{'token':[],'confirmed':True}),('list',[]),('evidence',{'id':[]})]:
            self.assertEqual(self.post('/api/memory/v2/'+path,data,token)[0],400)
