"""Synthetic regressions for conversation routing and explicit current-term retrieval."""
import unittest
import urllib.request
from yiban.memory.shared_dialogue import catalog, matching
from yiban.memory.retrieval import MemoryRetriever
import test_shared_http as fixture

class CompanionRoutingTests(unittest.TestCase):
    def topics(self):
        return catalog([
            {'id':'slogan','content':'某同学作“信任=责任，引领我们共赴未来”专题分享。','event_date':'2026-10-17','status':'pending'},
            {'id':'bag','content':'帆布包展示“2025级A班”。','status':'pending'},
            {'id':'books','content':'“书暖乡村，智启未来”捐书总量已确认。','status':'valid'},
            {'id':'reading','content':'2026年12月5日举行“周末读书会”。','event_date':'2026-12-05','status':'valid'},
        ])

    def test_pronouns_class_codes_and_incidental_bigrams_are_not_topic_anchors(self):
        for query in ('我们班第二届班长是谁？','25A班第一届班长是谁？','2026年11月对阵25F的比分？'):
            self.assertEqual(matching(query,self.topics()),[],query)
        self.assertEqual([t['title'] for t in matching('书暖乡村，智启未来捐书一共多少本？',self.topics())],['书暖乡村，智启未来'])

    def test_opponent_and_role_prevent_date_alone_choosing_unrelated_event(self):
        for query in ('2026年10月17日对阵26F比分多少？','2026年10月17日第二届班长是谁？'):
            self.assertEqual(matching(query,self.topics()),[])
        self.assertEqual([t['title'] for t in matching('12月5日那次',self.topics())],['周末读书会'])
        self.assertEqual([t['title'] for t in matching('读书会谁主持？',self.topics())],['周末读书会'])

    def test_current_query_promotes_newest_explicit_role_but_history_still_works(self):
        retriever=MemoryRetriever({'strategy':'lexical','relative_score_floor':.85})
        rows=[{'id':'old','version':1,'status':'pending','content':'本班第一届班长是角色甲。'},
              {'id':'new','version':1,'status':'valid','content':'第二届班委名单列班长为角色乙。'},
              {'id':'exec','version':1,'status':'valid','content':'第二届执行班长是角色丙。'}]
        self.assertEqual(retriever.retrieve('本班现在班长是谁？',rows,namespace='test')['hits'][0]['id'],'new')
        self.assertEqual(retriever.retrieve('第一届班长是谁？',rows,namespace='test')['hits'][0]['id'],'old')

class CompanionAssetHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.SharedHTTPTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.SharedHTTPTests.tearDownClass.__func__)

    def test_companion_script_is_served_and_in_page(self):
        base=f'http://127.0.0.1:{self.http.server_port}'
        with urllib.request.urlopen(base+'/companion-memory.js') as response:
            self.assertIn('text/javascript',response.headers['Content-Type'])
            script=response.read().decode()
        self.assertIn("sharedApi('/agent/ask'",script)
        self.assertNotIn("sharedApi('/agent/memory/confirm'",script)
        with urllib.request.urlopen(base+'/') as response:page=response.read().decode()
        self.assertIn('id="chatMemoryMode"',page)
        self.assertIn('src="/companion-memory.js"',page)
