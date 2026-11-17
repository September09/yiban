import unittest
from yiban.memory.retrieval import facet_supported, MemoryRetriever, explicit_match_bonus
from yiban.memory.read_only_agent import decision_schema

class EventSelectorsTests(unittest.TestCase):
    def test_distinct_terms_and_roles(self):
        self.assertFalse(facet_supported('第二届班长是谁？',{'content':'第一届班长是甲。'}))
        self.assertFalse(facet_supported('第二届班长是谁？',{'content':'第二届执行班长是乙。'}))
        self.assertTrue(facet_supported('第一届和第二届班长分别是谁？',{'content':'第二届班长是甲。'}))
        self.assertTrue(facet_supported('第2届班长是谁？',{'content':'第二届班长是甲。'}))
        self.assertTrue(facet_supported('谁担任班长？',{'content':'资料尚未记载相关职务。'}))

    def test_distinct_games_and_dates_without_guessing_missing_values(self):
        self.assertFalse(facet_supported('2025年11月1日对24D比分？',{'content':'2026年11月7日对25F比分38:49。'}))
        self.assertFalse(facet_supported('对24D比分？',{'content':'对阵25F，比分38:49。'}))
        self.assertTrue(facet_supported('2025年11月1日对24D比分？',{'content':'2025年11月1日对阵24D，以28:21获胜。'}))
        self.assertTrue(facet_supported('对26F比分？',{'content':'对阵26F，未给出最终比分。'}))
        self.assertTrue(facet_supported('2026年4月20日的活动？',{'content':'4月的活动，日期未知。'}))
        self.assertTrue(facet_supported('2026年6月20日的活动？',{'content':'6月19—20日两天的活动。'}))

    def test_no_finished_without_any_tool_and_comparison_reads_all(self):
        self.assertNotIn('finish',decision_schema(None,[],{},None)[1])
        self.assertIn('finish',decision_schema(None,[],{}, {'text':'时钟'})[1])
        self.assertNotIn('finish',decision_schema({},[{'id':'a'},{'id':'b'}],{1:{}},None,True)[1])

    def test_explicit_matches_rank_above_vague_records_without_admitting_unrelated(self):
        retriever=MemoryRetriever({'strategy':'lexical','relative_score_floor':.5})
        items=[{'id':'vague','version':1,'status':'pending','content':'本班班委选举班长活动顺利举行。'},
               {'id':'exact','version':1,'status':'valid','content':'第二届班委选举名单列班长为角色甲。'},
               {'id':'wrong','version':1,'status':'valid','content':'第一届班长为角色乙。'}]
        result=retriever.retrieve('第二届班长是谁？',items,namespace='synthetic')
        self.assertEqual(result['hits'][0]['id'],'exact')
        self.assertNotIn('wrong',[hit['id'] for hit in result['hits']])
        self.assertEqual(explicit_match_bonus('第2届班长是谁？',items[1]),.3)
        self.assertNotIn('finish',decision_schema({},[items[1]],{},None)[1])
