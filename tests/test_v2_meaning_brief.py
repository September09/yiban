"""Concise evidence fields and relevant display preserve facts and scope."""
import json
import unittest
from yiban.memory import v2_narrative
import test_v2_unseen24_repair as fixture

class MeaningBriefTests(unittest.TestCase):
    def test_two_original_purpose_fields_make_one_short_sentence(self):
        proof='松石制作木盒，作为共同航行的纪念。它提醒着我们承诺的分量与合作的价值。'
        result=v2_narrative.brief_object_meaning(proof)
        self.assertEqual(result,'它纪念的是共同航行，也让人记得承诺的分量与合作的价值。')
        self.assertLess(len(result),45)
        self.assertNotIn('亲手',result)

    def test_missing_ambiguous_or_uncertain_fields_are_not_rewritten(self):
        for proof in ['纪念物意义未记载。','可能作为共同航行的纪念，提醒我们合作。',
                      '作为共同航行的纪念；作为暑期出游的纪念，提醒大家合作的价值。',
                      '作为共同航行的纪念。']:
            self.assertIsNone(v2_narrative.brief_object_meaning(proof))

    def test_compact_purpose_keeps_current_record_reference_and_original(self):
        _,_,cup,_=fixture.UnseenRepairTests().seed()
        original='海水制成玻璃瓶送给柳叶，作为共同航行的纪念。它提醒我们承诺的分量。'
        result=json.loads(v2_narrative.reviewed_object_meaning('这件纪念物代表什么？',[cup],{'gift':original},{'facet_ids':['gift']}))
        self.assertEqual(result['references'],[1])
        self.assertEqual(result['answer'],'它纪念的是共同航行，也让人记得承诺的分量。')
        self.assertIn('它提醒我们',original)

    def test_display_preserves_both_scopes_ids_and_reference_numbers(self):
        sources=[{'id':'a','scope_kind':'personal','reference':2},
                 {'id':'noise','scope_kind':'team','reference':3},
                 {'id':'b','scope_kind':'team','reference':6}]
        self.assertEqual(v2_narrative.display_sources(sources,['a','b'],{}),[sources[0],sources[2]])
        self.assertEqual(len(sources),3)
        self.assertEqual(sources[2]['reference'],6)

    def test_unknown_display_requires_one_current_resolved_id(self):
        sources=[{'id':'a'},{'id':'noise'}]
        self.assertEqual(v2_narrative.display_sources(sources,[],{}),[])
        self.assertEqual(v2_narrative.display_sources(sources,[],{'focus':{'primary_ids':['a']}}),[sources[0]])
        self.assertEqual(v2_narrative.display_sources(sources,[],{'facet_ids':['a','noise']}),[])
        self.assertEqual(v2_narrative.display_sources(sources,[],{'facet_ids':['not-readable']}),[])
