"""Invented objects and people exercise context repair without demo answers."""
import json
import unittest
from types import SimpleNamespace
from yiban.memory import v2_dialogue, v2_intent, v2_grounding
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError
from test_v2_reasoning_guards import row,event

def personal(i,title,text):
    return dict(row(i,title,text),scope_kind='personal',scope_key='personal:x',scope_name='测试个人')

class ParaphraseRepairTests(unittest.TestCase):
    def seed(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError))
        scopes=[{'id':'t','version':1,'member_version':1}]
        outing=event('outing','秋季出游','hiking')
        gift=dict(row('gift','木盒留念','林溪制作木盒，赠给青禾与岚岚。',outing),scope_kind='team')
        other=personal('other','赛前准备','在旅馆准备第二天的装备。')
        plan,_,_,_=v2_dialogue.begin(service,'token',{'query':'秋季出游做的木盒是谁制作的？'},scopes,[gift,other])
        prior=v2_dialogue.finish(service,plan,[gift],['gift'])
        return service,scopes,gift,other,prior

    def test_prepare_and_recipient_follow_last_object_not_unrelated_preparation(self):
        service,scopes,gift,other,prior=self.seed()
        for q in ['这是谁准备的？','具体送给了哪两位同学？']:
            plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':q,'conversation_id':prior['id']},scopes,[gift,other])
            self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['gift'])
            raw=v2_dialogue.verified_followup(plan,q,found,found,{'gift':gift['content']})
            self.assertEqual(json.loads(raw)['references'],[1])
            prior=v2_dialogue.finish(service,plan,found,['gift'])

    def test_unknown_item_fields_keep_object_for_next_turn(self):
        service,scopes,gift,other,prior=self.seed()
        for q in ['每个人都收到一只吗？','做这份礼物花了多少元？','那它的尺寸和制作店铺有记录吗？']:
            plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':q,'conversation_id':prior['id']},scopes,[gift,other])
            self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['gift'])
            result=json.loads(v2_grounding.item_field_unknown(q,found,{'gift':gift['content']},plan))
            self.assertEqual(result['references'],[])
            prior=v2_dialogue.finish(service,plan,found,[])
            self.assertIsNotNone(prior['id'])

    def test_no_prior_object_requests_clarification_instead_of_inheriting_new_identity(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError))
        scopes=[{'id':'t','version':1,'member_version':1}]
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':'那它的尺寸呢？'},scopes,[])
        self.assertFalse(found)

    def test_scope_target_is_a_filter_not_an_access_grant(self):
        for q in ['换到我自己，开始备赛是怎么回事？','再说我自己，官方完赛成绩是多少？','我的训练搭档叫什么？']:
            self.assertEqual(v2_intent.scope_target(q),'personal')
        self.assertEqual(v2_intent.scope_target('咱班那份纪念物是谁做的？'),'team')
        self.assertIsNone(v2_intent.scope_target('我的训练伙伴和班级礼物分别是谁负责？'))
        self.assertTrue(v2_intent.personal_result('再说我自己，官方完赛成绩是多少？'))

    def test_personal_start_and_routine_place_paraphrases(self):
        rows=[personal('join','加入俱乐部','看到宣传后加入俱乐部。'),
              personal('places','日常训练环境','地点包括森林和河边。'),
              personal('other','备赛期间的变化','备赛时生活发生变化。')]
        for q in ['换到我自己，备战长跑最初是怎么开始的？','备赛起初如何开始？']:
            self.assertEqual([r['id'] for r in v2_intent.personal_facets(q,rows)],['join'])
        for q in ['平常都在哪些地方练跑？','平日通常在哪儿慢跑？']:
            self.assertEqual([r['id'] for r in v2_intent.personal_facets(q,rows)],['places'])

    def test_followup_never_rehydrates_an_unreadable_record(self):
        service,scopes,gift,other,prior=self.seed()
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':'这是谁准备的？','conversation_id':prior['id']},scopes,[other])
        self.assertNotIn('gift',[r['id'] for r in found])

    def test_new_named_domain_is_not_a_bare_object_followup(self):
        for q in ['那第二次生日会在哪里？','那天还有其他安排吗？','那班徽是谁设计的？','我的训练搭档是谁？']:
            self.assertFalse(v2_intent.item_followup(q))
        self.assertTrue(v2_intent.item_followup('这是谁准备的？'))

    def test_actual_cited_narrative_survives_alias_only_followup(self):
        service,scopes,gift,other,prior=self.seed()
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':'是谁把木片带回来的？','conversation_id':prior['id']},scopes,[gift,other])
        following=v2_dialogue.finish(service,plan,[gift],['gift'])
        self.assertIsNotNone(following['id'])
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':'做这份礼物花了多少元？','conversation_id':following['id']},scopes,[gift,other])
        self.assertEqual([r['id'] for r in found],['gift'])

    def test_absent_action_does_not_authorize_a_negative_answer(self):
        r=dict(row('g','礼物','林溪赠给青禾。'),scope_kind='team')
        self.assertTrue(v2_grounding.unsupported_negative('并没有送给其他同学。',[r],{'g':'林溪赠给青禾。'},['g']))
        self.assertTrue(v2_grounding.unsupported_negative('没有送给其他人。',[r],{'g':'我不是一个人在奋斗。'},['g']))
        self.assertFalse(v2_grounding.unsupported_negative('没有送给其他人。',[r],{'g':'记录明确说没有送给其他人。'},['g']))
        self.assertTrue(v2_grounding.unsupported_negative('没有送给其他人。',[r],{'g':'没有送给林溪。'},['g']))
        self.assertFalse(v2_grounding.unsupported_negative('不是看到了希望才坚持，而是坚持了才看到希望。',[r],{'g':'坚持的体会。'},['g']))

    def test_present_or_mixed_item_fields_keep_model_path(self):
        r=dict(row('g','木盒','木盒作为纪念。'),scope_kind='team')
        plan={'focus':{'primary_ids':['g']}}
        self.assertIsNone(v2_grounding.item_field_unknown('做这份礼物花了多少元？',[r],{'g':'费用为八十元。'},plan))
        self.assertIsNone(v2_grounding.item_field_unknown('那它的尺寸和制作店铺有记录吗？',[r],{'g':'尺寸为十厘米。'},plan))
        self.assertIsNone(v2_grounding.item_field_unknown('这份礼物的意义和费用是什么？',[r],{'g':'为了纪念同行。'},plan))


class SemanticFacetProofTests(unittest.TestCase):
    def test_semantic_field_cannot_be_dropped_by_literal_second_ranking(self):
        places=personal('forest','日常训练环境','地点包括森林和河边。')
        plan={'personal_facet':True,'personal_trait':'training_places','facet_ids':['forest']}
        self.assertEqual(v2_dialogue.expand_sources(plan,'平常在哪些地方练跑？',[places],[]),[places])
        self.assertEqual(v2_dialogue.expand_sources(plan,'平常在哪些地方练跑？',[],[]),[])

    def test_joining_cause_reuses_reviewed_evidence_not_unknown_date(self):
        r=personal('join','加入俱乐部的起因','叙述者看到宣传后加入俱乐部，后来坚持备赛。')
        plan={'personal_trait':'joining','facet_ids':['join']}
        quote={'join':'我看完宣传后加入了俱乐部，随后坚持备赛。'}
        raw=v2_dialogue.reviewed_personal_extract(plan,'备战最初是怎么开始的？',[r],quote)
        self.assertIn('你看到宣传后加入俱乐部',json.loads(raw)['answer'])
        self.assertEqual(json.loads(raw)['references'],[1])
        for q in ['什么时候加入俱乐部？','哪一年加入俱乐部？','是谁邀请我加入俱乐部？']:
            self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,q,[r],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'怎么加入俱乐部的？',[{**r,'verification':'pending'}],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'怎么加入俱乐部的？',[r],{}))
