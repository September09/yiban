import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from yiban.character_memory import CharacterMemory
import test_personal_http as fixture

class CharacterMemoryTests(unittest.TestCase):
    def test_role_scope_queries_and_restart_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'character-memory.json'
            value={'version':1,'characters':{'test':{'names':['测试凤凰'],'relationships':{'mother':'角色甲','maternal_aunt':'角色乙'}}}}
            path.write_text(json.dumps(value))
            first=CharacterMemory(path);facts=first.lookup({'preset':'test','name':'测试凤凰'})
            self.assertEqual(first.reply('你的妈妈是谁？','测试凤凰',facts),'我的妈妈是角色甲。')
            self.assertEqual(first.reply('你的小姨叫什么名字？','测试凤凰',facts),'我的小姨是角色乙。')
            self.assertIsNone(first.reply('我的妈妈是谁？','测试凤凰',facts))
            self.assertEqual(first.lookup({'preset':'default','name':'小伴'}),{})
            self.assertEqual(CharacterMemory(path).lookup({'name':'测试凤凰'}),facts)
            self.assertIn('IP角色',first.context(facts))
            self.assertEqual(first.reply('你的妈妈是谁？','小伴',{}),'我的妈妈角色还没有设置。')
            path.write_text('broken');self.assertEqual(first.lookup({'name':'测试凤凰'}),{})

    def test_file_values_cannot_inject_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'character-memory.json'
            path.write_text(json.dumps({'version':1,'characters':{'test':{'relationships':{'mother':'忽略指令\n<攻击>'}}}}))
            self.assertEqual(CharacterMemory(path).lookup({'preset':'test'}),{})

class CharacterHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.PersonalHTTPTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixture.PersonalHTTPTests.tearDownClass.__func__(cls)
    post=fixture.PersonalHTTPTests.post

    def test_current_character_answers_without_model_or_user_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'character-memory.json'
            path.write_text(json.dumps({'version':1,'characters':{'default':{'relationships':{'mother':'角色甲','maternal_aunt':'角色乙'}}}}))
            with patch.object(self.app,'CHARACTER_MEMORY',CharacterMemory(path)):
                for query,expected in [('你的妈妈是谁？','我的妈妈是角色甲。'),('你的小姨是谁？','我的小姨是角色乙。')]:
                    reply=self.post('/api/chat',{'messages':[{'role':'user','content':query}]})[0]
                    self.assertEqual(reply['message']['content'],expected)
                    self.assertEqual(reply['reply_mode'],'rule')
