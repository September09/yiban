"""Joint intent regressions use invented people, objects, amounts and diagnoses."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from yiban.memory import v2_intent, v2_dialogue, v2_grounding, v2_query
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError
from test_v2_reasoning_guards import row
import test_memory_v2_dialogue as fixture


def personal(identity,title,content):
    return dict(row(identity,title,content),scope_kind='personal',scope_key='personal:test',scope_name='个人测试')


class JointIntentTests(unittest.TestCase):
    def test_mixed_questions_split_but_one_object_followup_does_not(self):
        self.assertEqual(v2_intent.questions('贝壳盒是谁做的，我的陪练伙伴是谁？'),['贝壳盒是谁做的','我的陪练伙伴是谁？'])
        self.assertEqual(v2_intent.questions('是谁做的，送给了谁？'),[])
        self.assertEqual(v2_intent.questions('第二届班长和执行班长分别是谁？'),[])
        self.assertEqual(v2_intent.questions('我的女儿叫什么，名字有什么寓意？'),[])
        self.assertEqual(v2_intent.questions('贝壳盒的意义，和我女儿名字的期望，分别是什么？'),['贝壳盒的意义是什么？','我女儿名字的期望是什么？'])

    def test_daily_communication_does_not_bind_one_dated_episode(self):
        rows=[personal('daily','跑步前沟通','每次跑步前与家人沟通。'),personal('day','一次训练沟通','某晚沟通后获支持。')]
        found=v2_intent.personal_facets('出门锻炼前，我和谁商量？',rows)
        self.assertEqual([r['id'] for r in found],['daily'])
        raw=v2_grounding.attribute_boundary('出门锻炼前，我和谁商量？',found,{'daily':'每次跑步前都和家人沟通。'},{} )
        self.assertIn('和家人沟通',json.loads(raw)['answer'])

    def test_personal_attributes_survive_team_event_distractors(self):
        rows=[dict(row('team','星桥远足','团队完成星桥远足。'),scope_kind='team'),personal('weight','身体变化','体重减轻约四公斤。'),personal('partner','训练搭子','与松石一起训练。')]
        self.assertEqual([r['id'] for r in v2_intent.personal_facets('远足后我的体重有什么变化？',rows)],['weight'])
        self.assertEqual([r['id'] for r in v2_intent.personal_facets('我的训练搭档是谁？',rows)],['partner'])
        self.assertFalse(v2_intent.personal_facets('团队成员分别和谁搭档？',rows))

    def test_record_context_retains_only_ids_and_survives_unknown(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError));scopes=[{'id':'personal:test','version':1,'member_version':0}]
        rows=[personal('child','取名与名字期望','女儿取名青禾，期望保持好奇心。')]
        plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':'女儿名字有什么寓意？'},scopes,rows)
        self.assertIsNone(clarify);prior=v2_dialogue.finish(service,plan,found,['child'])
        self.assertIsNotNone(prior['id'])
        plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':'那她具体哪天出生？','conversation_id':prior['id']},scopes,[],context_items=rows)
        self.assertIsNone(clarify);self.assertFalse(found)
        prior=v2_dialogue.finish(service,plan,[],[])
        plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':'这个名字有什么期望？','conversation_id':prior['id']},scopes,rows)
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['child'])
        self.assertNotIn('content',v2_intent.record_topic(found[0]))

    def test_detail_questions_never_force_summary_over_original(self):
        rows=[dict(personal('child','名字','名字有寓意。'),reference=1)]
        self.assertIsNone(v2_dialogue.extractive_overview({'facet_ids':['child']},'这个名字表达什么期望？',rows))
        raw=v2_grounding.attribute_boundary('这个名字表达什么期望？',rows,{'child':'取名青禾，期望保持好奇心。'},{} )
        self.assertIn('保持好奇心',json.loads(raw)['answer'])

    def test_possible_diagnosis_and_consulting_are_not_completed_actions(self):
        r=personal('health','康复评估','康复师判断有可能是肌腱劳损。')
        raw=v2_grounding.attribute_boundary('我已经被确诊了吗？',[r],{'health':'康复师判断有可能是肌腱劳损。'},{} )
        self.assertIn('有可能',json.loads(raw)['answer']);self.assertIn('没有记载确定',json.loads(raw)['answer'])
        r=personal('tape','肌贴请教','向青禾请教膝关节肌贴。')
        raw=v2_grounding.attribute_boundary('谁帮我贴了肌贴？',[r],{'tape':'我向青禾请教膝关节肌贴，松石帮我贴脚踝。'},{} )
        self.assertIn('向青禾请教',json.loads(raw)['answer']);self.assertNotIn('青禾帮',json.loads(raw)['answer'])

    def test_missing_spouse_name_and_medicine_cannot_use_other_attributes(self):
        r=personal('child','女儿名字','女儿叫青禾。')
        self.assertFalse(v2_intent.personal_facets('妻子的名字是什么？',[r]))
        for query,quote in [('妻子的名字是什么？','女儿叫青禾。'),('康复师开的药名和剂量是什么？','康复师判断有可能是劳损。')]:
            raw=v2_grounding.attribute_boundary(query,[r],{'child':quote},{})
            self.assertEqual(json.loads(raw)['references'],[])

    def test_absolute_weight_is_not_derived_from_loss(self):
        r=personal('weight','体重变化','体重下降约四公斤。')
        raw=v2_grounding.attribute_boundary('前后分别是多少公斤？',[r],{'weight':'我瘦了四公斤左右。'},{} )
        body=json.loads(raw)['answer'];self.assertIn('四公斤',body);self.assertIn('没有提供',body)
        self.assertIsNone(v2_grounding.attribute_boundary('前后分别是多少公斤？',[r],{'weight':'训练前体重为六十公斤，训练后体重为五十六公斤。'},{}))


class JointDispatchTests(unittest.TestCase):
    setUp=fixture.V2FollowupTests.setUp
    tearDown=fixture.V2FollowupTests.tearDown
    publish=fixture.V2FollowupTests.publish

    def test_mixed_answers_keep_real_citations_from_both_authorized_scopes(self):
        doc=fixture.packet();doc['records']=[{**doc['records'][1],'key':'personal-partner','title':'训练搭子','summary':'松石是训练搭子。','event_key':None,'sensitivity':'personal'}]
        plan=self.service.preview(self.token,{'scope_kind':'personal','document':doc});self.service.confirm(self.token,{'scope_kind':'personal','token':plan['token'],'confirmed':True,'acknowledge_warnings':True})
        result=self.service.dispatch('ask',self.token,{'query':'贝壳盒是谁做的，我的训练搭档是谁？'})
        cited=[r for r in result['sources'] if r['id'] in result['answer_source_ids']]
        self.assertEqual({r['scope_kind'] for r in cited},{'personal','team'})
        self.assertIn('松石',result['answer']);self.assertIn('周宁',result['answer'])
        self.assertEqual(len({r['reference'] for r in result['sources']}),len(result['sources']))

    def test_revocation_between_subquestions_rejects_entire_answer(self):
        token=self.users['reader']['token'];calls=[0]
        def revoke():
            calls[0]+=1
            if calls[0]==1:self.shared.set_member(self.token,{'space_id':self.space,'account_id':self.users['reader']['account']['id']},remove=True)
        self.model.callback=revoke
        with self.assertRaises(SharedError):self.service.dispatch('ask',token,{'query':'贝壳盒是谁做的，我的训练搭档是谁？'})
