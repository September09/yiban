"""Exercise memory commands and cancellation through actual HTTP routes."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yiban.settings import ROOT, load_settings
from test_backends import FakeModel


class PersonalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.model = ThreadingHTTPServer(('127.0.0.1', 0), FakeModel)
        cls.model.response_status = 200
        cls.model.response_body = b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
        config = load_settings(ROOT/'config/board-text.example.json')
        config['llm'].update(base_url=f'http://127.0.0.1:{cls.model.server_port}/v1', model='test-chat')
        config['memory']['database'] = str(Path(cls.temp.name)/'memory.sqlite3')
        path = Path(cls.temp.name)/'config.json'
        path.write_text(json.dumps(config))
        with patch.dict(os.environ, {'ASSISTANT_CONFIG': str(path)}):
            spec=importlib.util.spec_from_file_location('feature_test_server', ROOT/'server.py')
            cls.app=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.app)
        cls.app.Handler.log_message=lambda *args: None
        cls.http=ThreadingHTTPServer(('127.0.0.1', 0), cls.app.Handler)
        cls.app.PORT=cls.http.server_port
        cls.threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (cls.model,cls.http)]
        for thread in cls.threads: thread.start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.http,cls.model): server.shutdown();server.server_close()
        for thread in cls.threads: thread.join()
        cls.temp.cleanup()

    def post(self, path, data):
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=5) as response:
            return [json.loads(line) for line in response.read().splitlines()]

    def test_chat_conflict_and_edit_history_require_confirmation(self):
        a=self.post('/api/profiles/create',{'name':'冲突甲'})[0]['id']
        b=self.post('/api/profiles/create',{'name':'冲突乙'})[0]['id']
        first=self.post('/api/memory/save',{'profile_id':a,'content':'我喜欢咖啡'})[0]
        reply=self.post('/api/chat',{'profile_id':a,'messages':[{'role':'user','content':'记住：我不喜欢咖啡'}]})[0]
        self.assertFalse(reply['memory_changed'])
        self.assertIn('尚未保存',reply['message']['content'])
        self.assertEqual(len(self.app.STORE.memories(a)),1)
        self.post('/api/memory/review/confirm',{'profile_id':a,'token':reply['memory_review']['token'],'confirmed':True,'resolution':'replace','replace_id':first['id']})
        history=self.post('/api/memory/history',{'profile_id':a,'id':first['id']})[0]
        self.assertEqual(history['entries'][0]['after_content'],'我不喜欢咖啡')
        with self.assertRaises(urllib.error.HTTPError):self.post('/api/memory/history',{'profile_id':b,'id':first['id']})
        edit=self.post('/api/memory/save',{'profile_id':a,'id':first['id'],'content':'新草稿','expected_revision':0})[0]
        self.assertTrue(edit['review']['stale'])
        self.assertEqual(self.app.STORE.memories(a)[0]['content'],'我不喜欢咖啡')

    def test_import_requires_preview_confirmation_and_original_target(self):
        a = self.post('/api/profiles/create', {'name':'恢复目标'})[0]['id']
        b = self.post('/api/profiles/create', {'name':'恢复其他目标'})[0]['id']
        document = self.post('/api/memory/export', {'profile_id':a})[0]
        document['memories'] = [dict(content='导入内容', source='manual', created=None, updated='2026-01-01 00:00:00')]
        preview = self.post('/api/memory/import/preview', {'profile_id':a,'document':document})[0]
        self.assertEqual(self.app.STORE.memories(a), [])
        for data in ({'profile_id':a,'token':preview['token']}, {'profile_id':b,'token':preview['token'],'confirmed':True}):
            with self.assertRaises(urllib.error.HTTPError) as error:self.post('/api/memory/import/confirm',data)
            self.assertEqual(error.exception.code,400)
        result=self.post('/api/memory/import/confirm',{'profile_id':a,'token':preview['token'],'confirmed':True})[0]
        self.assertEqual(result['imported'],1)
        self.assertEqual(self.app.STORE.memories(b),[])
        request=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/memory/import/preview',
            data=json.dumps({'profile_id':a,'document':document}).encode(),headers={'Origin':'https://example.com'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request,timeout=5)
        self.assertEqual(error.exception.code,403)

    def test_export_route_and_server_assigned_source(self):
        a = self.post('/api/profiles/create', {'name':'导出甲'})[0]['id']
        b = self.post('/api/profiles/create', {'name':'导出乙'})[0]['id']
        self.post('/api/memory/save', {'profile_id':a,'content':'手动记忆','source':'conversation'})
        self.post('/api/chat', {'profile_id':a,'messages':[{'role':'user','content':'记住：对话记忆'}]})
        self.post('/api/memory/save', {'profile_id':b,'content':'其他人的资料'})
        self.app.STORE.enroll(a, [1,0,0], 'private-voiceprint')
        result = self.post('/api/memory/export', {'profile_id':a})[0]
        self.assertEqual({m['content']:m['source'] for m in result['memories']},
                         {'手动记忆':'manual','对话记忆':'conversation'})
        self.assertEqual(result['profile']['id'], a)
        self.assertNotIn('其他人的资料', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('private-voiceprint', json.dumps(result))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/memory/export', {'profile_id':''})
        self.assertEqual(error.exception.code, 400)
        request = urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/memory/export',
            data=json.dumps({'profile_id':a}).encode(), headers={'Content-Type':'application/json','Origin':'https://example.com'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 403)

    def test_memory_commands_and_model_context_are_profile_scoped(self):
        a=self.post('/api/profiles/create',{'name':'用户甲'})[0]['id']
        b=self.post('/api/profiles/create',{'name':'用户乙'})[0]['id']
        def chat(profile,text):
            return self.post('/api/chat',{'profile_id':profile,'messages':[{'role':'user','content':text}]})
        result=chat(a,'记住：我喜欢乌龙茶')
        self.assertTrue(result[0]['memory_changed'])
        self.assertEqual(len(self.post('/api/memory/list',{'profile_id':a})[0]['memories']),1)
        self.assertEqual(self.post('/api/memory/list',{'profile_id':b})[0]['memories'],[])
        chat(a,'我喜欢乌龙茶吗')
        self.assertIn('乌龙茶',self.model.request_body['messages'][0]['content'])
        chat(b,'推荐喝什么')
        self.assertNotIn('乌龙茶',self.model.request_body['messages'][0]['content'])
        chat('', '推荐喝什么')
        self.assertNotIn('乌龙茶',self.model.request_body['messages'][0]['content'])
        chat(a,'忘掉：我喜欢乌龙茶')
        self.assertEqual(self.post('/api/memory/list',{'profile_id':a})[0]['memories'],[])

    def test_cancel_before_chat_rejects_late_request(self):
        identity='http-cancel-before-start'
        self.post('/api/turn/cancel',{'turn_id':identity})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/chat',{'turn_id':identity,'messages':[{'role':'user','content':'你好'}]})
        self.assertEqual(error.exception.code,400)
        self.assertIn('打断',json.load(error.exception)['error'])
        error.exception.close()

    def test_current_clock_refreshes_across_midnight_without_memory_writes(self):
        zone = ZoneInfo('Asia/Shanghai')
        profile = self.post('/api/profiles/create', {'name': '日期测试'})[0]['id']
        with patch.object(self.app, 'datetime') as clock:
            clock.now.side_effect = [datetime(2026, 11, 8, 23, 59, 59, tzinfo=zone),
                                     datetime(2026, 11, 9, 0, 0, 1, tzinfo=zone)]
            for date, weekday in [('2026年11月8日', '星期日'), ('2026年11月9日', '星期一')]:
                self.post('/api/chat', {'profile_id': profile, 'messages': [
                    {'role': 'user', 'content': '今天多少号？'},
                    {'role': 'assistant', 'content': '不能联网，所以不知道日期。'},
                    {'role': 'user', 'content': '请说明日期信息能否离线获取。'}]})
                context = self.model.request_body['messages'][0]['content']
                self.assertIn(date, context)
                self.assertIn(weekday, context)
                self.assertIn('Asia/Shanghai', context)
                self.assertIn('不需要联网', context)
            self.assertEqual(clock.now.call_count, 2)
            clock.now.assert_called_with(zone)
        self.assertEqual(self.post('/api/memory/list', {'profile_id': profile})[0]['memories'], [])

    def test_clock_answers_do_not_depend_on_model_or_incorrect_history(self):
        with patch.object(self.app, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 8, 10, 25, tzinfo=ZoneInfo('Asia/Shanghai'))
            for question in ('今天多少号？', '今天是星期几？', '再确认一下今天的日期和星期几。'):
                result = self.post('/api/chat', {'messages': [
                    {'role': 'user', 'content': '今天多少号？'},
                    {'role': 'assistant', 'content': '不能联网，不知道。'},
                    {'role': 'user', 'content': question}]})[0]
                self.assertEqual(result['message']['content'], '今天是 2026 年 11 月 8 日，星期日。')
                self.assertFalse(result['memory_changed'])
            self.assertEqual(self.app.clock_reply('现在几点？'), '现在是北京时间 10 点 25 分。')
            for unrelated in ('今天的天气怎么样？', '今天几号的航班比较便宜？', '记住：今天是我的生日', '2020年今天是星期几？'):
                self.assertIsNone(self.app.clock_reply(unrelated))

    def test_identity_uses_current_profile_even_without_memories(self):
        a = self.post('/api/profiles/create', {'name': '身份甲'})[0]['id']
        b = self.post('/api/profiles/create', {'name': '身份乙'})[0]['id']
        for profile, name in ((a, '身份甲'), (b, '身份乙'), ('', None)):
            result = self.post('/api/chat', {'profile_id': profile, 'messages': [
                {'role': 'user', 'content': '我是谁？'},
                {'role': 'assistant', 'content': '我不知道你是谁。'},
                {'role': 'user', 'content': '你知道我是谁吗？'}]})[0]
            text = result['message']['content']
            self.assertFalse(result['memory_changed'])
            if name:
                self.assertEqual(text, f'当前对话的用户档案是「{name}」。')
                self.assertEqual(self.post('/api/memory/list', {'profile_id': profile})[0]['memories'], [])
            else:
                self.assertIn('访客', text)
                self.assertNotIn('身份甲', text)
                self.assertNotIn('身份乙', text)
        for text in ('你知道我是谁的朋友吗？', '记住：我叫小明', '我是谁这个问题很哲学'):
            self.assertIsNone(self.app.STORE.identity_reply(a, text))

    def test_voice_identity_questions_use_profile_or_visitor(self):
        profile = self.post('/api/profiles/create', {'name': '测试档案乙'})[0]['id']
        for question in ('现在你可以通过这样的声音来猜猜我是谁了吗？', '你能凭我的声纹认出我吗？'):
            for identity in (profile, ''):
                with self.subTest(question=question, has_profile=bool(identity)):
                    result = self.post('/api/chat', {
                        'profile_id': identity, 'messages': [{'role': 'user', 'content': question}],
                    })[0]
                    if identity:
                        self.assertEqual(result['message']['content'], '当前对话的用户档案是「测试档案乙」。')
                    else:
                        self.assertIn('访客', result['message']['content'])
        self.assertIsNone(self.app.STORE.identity_reply(profile, '通过声音判断我是谁的朋友吗？'))

    def test_salutation_persists_without_changing_memories_or_voiceprint(self):
        identity = self.post('/api/profiles/create', {'name': '称呼测试'})[0]['id']
        self.app.STORE.save(identity, '偏好简短回答')
        self.app.STORE.enroll(identity, [1, 0], 'test-model')
        result = self.post('/api/profiles/salutation', {'profile_id': identity, 'salutation': ' 王先生 '})[0]
        self.assertEqual(result['salutation'], '王先生')
        self.assertEqual(result['name'], '称呼测试')
        profiles = self.post('/api/profiles/list', {})[0]['profiles']
        saved = next(p for p in profiles if p['id'] == identity)
        self.assertTrue(saved['enrolled'])
        self.assertEqual(saved['salutation'], '王先生')
        self.assertEqual(len(self.post('/api/memory/list', {'profile_id': identity})[0]['memories']), 1)
        self.assertIn('王先生', self.app.STORE.context(identity, '你好'))
        for value in (None, 123, '称'*41, '王先生\n其他内容'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post('/api/profiles/salutation', {'profile_id': identity, 'salutation': value})
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        self.post('/api/profiles/salutation', {'profile_id': identity, 'salutation': ''})
        self.assertEqual(self.app.STORE.profile(identity)['salutation'], '')

    def test_consent_required_and_no_audio_written(self):
        identity=self.post('/api/profiles/create',{'name':'声纹测试'})[0]['id']
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/speaker/enroll',{'profile_id':identity,'consent':False,'recordings':['','','']})
        self.assertIn('同意',json.load(error.exception)['error'])
        error.exception.close()
        profiles=self.post('/api/profiles/list',{})[0]['profiles']
        self.assertFalse(next(p for p in profiles if p['id']==identity)['enrolled'])
        self.assertEqual(list(Path(self.temp.name).glob('*.wav')),[])


if __name__=='__main__': unittest.main()
