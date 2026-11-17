"""Field binding and elliptical followups with invented data; no production writes."""
import copy
import hashlib
import json
import unittest
import test_memory_v2 as facts
import test_memory_v2_dialogue as dialogue
from yiban.memory.v2_facts import UNKNOWN


class PartialScoreTests(unittest.TestCase):
    def setUp(self):
        self.fx=facts.V2Tests();self.fx.setUp()
        row=next(r for r in self.fx.doc['records'] if r['key']=='game-two-fact')
        row['fact']['value']['result']='loss'
        self.fx.publish()
    def tearDown(self):self.fx.tearDown()
    def test_result_and_unknown_score_are_separate(self):
        r=self.fx.ask('对阵丙队的结果和比分是什么？')
        self.assertIn('失利',r['answer']);self.assertIn('比分未记载',r['answer'])
        self.assertNotIn('38比49',r['answer']);self.assertEqual(r['calculation']['kind'],'structured_partial')
        self.assertEqual(len(r['answer_source_ids']),1);self.assertEqual(self.fx.model.seen,[])
    def test_missing_margin_keeps_known_result_without_inventing_difference(self):
        r=self.fx.ask('对阵丙队输了几分？')
        self.assertIn('失利',r['answer']);self.assertIn('分差暂时无法确认',r['answer'])
        self.assertNotIn('11分',r['answer'])
    def test_two_games_keep_their_own_known_fields(self):
        r=self.fx.ask('篮球对阵乙队和丙队，比分和胜负分别是什么？')
        self.assertIn('38比49',r['answer']);self.assertIn('丙队：比分未记载，失利',r['answer'])
        self.assertEqual(len(r['answer_source_ids']),2)


class BareQuantityTests(unittest.TestCase):
    def setUp(self):
        import test_memory_v2_selectors as fixture
        self.fx=fixture.SelectorTests();self.fx.setUp();self.fx.book_components();self.fx.publish()
    def tearDown(self):self.fx.tearDown()
    def test_subtype_then_total_do_not_depend_on_model_generation(self):
        a=self.fx.ask('捐书筛选后总共是多少本？')
        b=self.fx.ask('其中新书有多少本？',a)
        c=self.fx.ask('二手书呢？',b)
        self.assertIn('217本',c['answer'])
        d=self.fx.ask('所以筛选后最终总数是500多本吗？',c)
        self.assertIn('131+217=348本',d['answer']);self.assertEqual(len(d['answer_source_ids']),2)
        self.assertEqual(self.fx.fx.model.seen,[])


class SubjectDateTests(unittest.TestCase):
    def setUp(self):
        self.fx=facts.V2Tests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def note(self,key,summary,quote=None):
        original=quote if quote is not None else summary
        source={'key':key,'name':'虚构生日原文','kind':'original','text':original,'sha256':hashlib.sha256(original.encode()).hexdigest()}
        self.fx.doc['sources'].append(source)
        self.fx.doc['records'].append({'key':key,'type':'reflection','title':summary,'summary':summary,'participants':['a'],
            'evidence':[{'source_key':key,'start':0,'end':len(original),'text':original}]})
    def test_child_date_does_not_use_collective_party(self):
        self.note('party','角色甲参加了2026年5月6日的集体生日会，为18位同学庆生。')
        self.fx.publish()
        r=self.fx.ask('角色甲孩子的出生日期是什么？')
        self.assertEqual(r['answer'],UNKNOWN);self.assertEqual(r['answer_source_ids'],[]);self.assertEqual(r['sources'],[])
        self.assertEqual(self.fx.model.seen,[])
    def test_own_date_does_not_use_collective_party_date(self):
        self.note('party','角色甲参加了2026年5月6日的集体生日会，为18位同学庆生。')
        self.fx.publish()
        r=self.fx.ask('角色甲的生日是什么时候？')
        self.assertEqual(r['answer'],UNKNOWN);self.assertEqual(r['answer_source_ids'],[])
    def test_explicit_authorized_child_date_remains_answerable(self):
        self.note('child','角色甲的孩子出生日期为2026年5月8日。')
        self.fx.publish(visibility='selected')
        r=self.fx.ask('角色甲孩子的出生日期是什么？')
        self.assertIn('2026年5月8日',r['answer']);self.assertTrue(r['answer_source_ids'])
        other=self.fx.ask('角色甲孩子的出生日期是什么？',self.fx.reader)
        self.assertEqual(other['answer'],UNKNOWN);self.assertEqual(other['sources'],[])
    def test_matching_summary_cannot_replace_unrelated_original_quote(self):
        self.note('bad-date','角色甲的孩子出生日期为2026年5月8日。','角色甲参加了2026年5月8日的集体生日会。')
        self.fx.publish()
        r=self.fx.ask('角色甲孩子的出生日期是什么？')
        self.assertEqual(r['answer'],UNKNOWN);self.assertEqual(r['answer_source_ids'],[])
    def test_other_family_member_cannot_supply_child_date(self):
        self.note('mother','角色甲的母亲生日是5月8日。')
        self.fx.publish()
        r=self.fx.ask('角色甲孩子的出生日期是什么？')
        self.assertEqual(r['answer'],UNKNOWN);self.assertEqual(r['answer_source_ids'],[])


class EllipticalFollowupTests(unittest.TestCase):
    def setUp(self):
        self.fx=dialogue.V2FollowupTests();self.fx.setUp();self.fx.confirm_gift()
    def tearDown(self):self.fx.tearDown()
    def test_bare_recipient_question_keeps_event_and_exact_reference(self):
        a=self.fx.first();r=self.fx.ask('送给谁了？',a['conversation']['id'])
        self.assertIn('岚岚和青禾',r['answer']);self.assertNotIn('沈溪',r['answer'])
        self.assertEqual([s['key'] for s in r['sources'] if s['id'] in r['answer_source_ids']],['gift'])
        self.assertEqual(r['answer_method'],'verified_excerpt')
    def test_bare_recipient_without_topic_does_not_guess(self):
        r=self.fx.ask('送给谁了？')
        self.assertIn('请先说明',r['answer']);self.assertEqual(r['sources'],[])
    def test_why_this_object_stays_after_unknown_cost(self):
        a=self.fx.first()
        original=self.fx.model.chat
        def model(messages,**kwargs):
            if '多少钱' in messages[-1]['content']:
                yield {'message':{'content':json.dumps({'answer':UNKNOWN,'references':[]})},'done':True}
            else:
                yield from original(messages,**kwargs)
        self.fx.model.chat=model
        b=self.fx.ask('每个贝壳盒花了多少钱？',a['conversation']['id'])
        self.assertEqual(b['answer'],UNKNOWN)
        r=self.fx.ask('为什么要送这个纪念品？',b['conversation']['id'])
        self.assertEqual({s['event_key'] for s in r['sources']},{'walk'})
        self.assertNotIn('胸针',r['answer'])
    def test_inside_object_keeps_primary_record_over_lexical_distractors(self):
        a=self.fx.first()
        b=self.fx.ask('送给谁了？',a['conversation']['id'])
        r=self.fx.ask('里面的贝壳从哪里带回来的？',b['conversation']['id'])
        self.assertEqual([s['key'] for s in r['sources']],['gift'])
        self.assertTrue(r['answer_source_ids'])
    def test_explicit_new_event_still_overrides_pronoun(self):
        a=self.fx.first();r=self.fx.ask('那创意晚宴胸针是谁设计的？',a['conversation']['id'])
        self.assertIn('沈溪',r['answer']);self.assertNotIn('周宁',r['answer'])


if __name__=='__main__':unittest.main()
