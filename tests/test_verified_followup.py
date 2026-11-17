"""Narrow, reviewed extractive path; no real text, database or model required."""
import copy
import json
import unittest
from yiban.memory.v2_dialogue import verified_followup


class VerifiedFollowupTests(unittest.TestCase):
    def setUp(self):
        self.row={'id':'gift','reference':2,'verification':'confirmed','fact':None,
                  'content':'报道记载周宁制作贝壳盒，赠予岚岚和青禾。'}
        self.plan={'focus':{'primary_ids':['gift']}}
        self.quotes={'gift':'周宁制作贝壳盒，赠予岚岚和青禾。'}

    def get(self,query='那是谁制作的？',items=None,sources=None,plan=None,quotes=None):
        return verified_followup(plan or self.plan,query,items if items is not None else [self.row],
                                 sources if sources is not None else [self.row],quotes if quotes is not None else self.quotes)

    def test_output_is_verbatim_reviewed_statement_with_current_reference(self):
        for query in ['那是谁制作的？','他送给谁了？','她赠予谁了？','那么是谁做的呢？']:
            result=json.loads(self.get(query));self.assertEqual(result['references'],[2])
            self.assertEqual(result['answer'],'周宁制作贝壳盒，赠予岚岚和青禾。')

    def test_other_intents_do_not_short_circuit(self):
        for query in ['所以是不是送了两个贝壳盒？','他为什么送给她？','是谁制作的，花了多少元？','下次送给谁？','并不是他制作的吗？','谁说是他制作的？','那是谁买的？']:
            self.assertIsNone(self.get(query),query)

    def test_unknown_or_uncertain_statement_never_becomes_an_answer(self):
        for text in ['制作人不详。','可能是周宁制作。','周宁不是制作人。','没有记载制作人。','谁制作的？','不知道谁制作。']:
            self.row['content']=text;self.assertIsNone(self.get(),text)

    def test_exact_evidence_required_and_negative_evidence_falls_back(self):
        for quote in ['', '这是一次远足。','周宁并未制作贝壳盒。','不是周宁制作的。']:
            self.assertIsNone(self.get(quotes={'gift':quote}),quote)

    def test_absent_or_multiple_anchors_and_missing_source_fail_closed(self):
        for plan in [{'focus':{}},{'focus':{'primary_ids':['gift','other']}}]:self.assertIsNone(self.get(plan=plan))
        self.assertIsNone(self.get(sources=[]))
        self.assertIsNone(self.get(items=[]))

    def test_candidates_not_in_top_hits_still_prevent_confident_shortcut(self):
        other={**self.row,'id':'other','content':'青禾设计了另一个盒子。','verification':'pending'}
        self.assertIsNone(self.get(items=[self.row,other]))

    def test_requires_confirmed_non_structured_short_record(self):
        for value in ['pending','disputed']:
            self.row['verification']=value;self.assertIsNone(self.get())
        self.row['verification']='confirmed';self.row['fact']={'predicate':'holds_role'};self.assertIsNone(self.get())
        self.row['fact']=None;self.row['content']='周宁制作贝壳盒。'*50;self.assertIsNone(self.get())
