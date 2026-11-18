"""Personal answer presentation stays evidence-bound; no real demo answers."""
import json
import unittest
from types import SimpleNamespace
from yiban.memory import v2_dialogue,v2_intent
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError
from test_v2_paraphrase_repair import personal

class WordingTests(unittest.TestCase):
    def test_location_overview_never_bypasses_the_brief_verified_path(self):
        r=personal('p','日常训练地点','地点包括森林、河边，也利用车库训练；这是阶段总结，不按地点累计活动次数。')
        plan={'personal_facet':True,'personal_trait':'training_places','facet_ids':['p']}
        query='平常都在哪些地方练跑？'
        self.assertIsNone(v2_dialogue.extractive_overview(plan,query,[r]))
        result=json.loads(v2_dialogue.reviewed_personal_extract(plan,query,[r],{'p':'我在森林、河边和车库训练。'}))
        self.assertEqual(result['answer'],'你的训练地点包括森林、河边，也在车库练过。')
        self.assertEqual(result['references'],[1])
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,query,[{**r,'verification':'pending'}],{'p':'我在河边训练。'}))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'平常在哪训练，每次几公里？',[r],{'p':'我在森林训练。'}))

    def test_partner_paraphrase_anchors_support_without_adopting_first_person(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError));scopes=[{'id':'p','version':1,'member_version':1}]
        r=personal('partner','训练搭子','训练搭子林溪持续鼓励，帮助叙述者在工作繁忙时继续训练。')
        q='经常陪我练、鼓励我坚持的伙伴是谁？'
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':q},scopes,[r])
        self.assertEqual(plan['personal_trait'],'partner')
        previous=v2_dialogue.finish(service,plan,found,['partner'])
        query='临近比赛又忙工作的时候，他怎么支持我？'
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':query,'conversation_id':previous['id']},scopes,[r])
        proof={'partner':'训练搭子林溪一直鼓励我在繁忙时继续训练，我不是一个人在战斗。'}
        result=json.loads(v2_dialogue.reviewed_personal_extract(plan,query,found,proof))
        self.assertIn('帮助你',result['answer']);self.assertNotIn('帮助我',result['answer'])
        self.assertNotIn('叙述者',result['answer']);self.assertEqual(result['references'],[1])
        self.assertIn('鼓励我',proof['partner'])

    def test_support_context_does_not_restore_an_unreadable_or_uncertain_partner(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError));scopes=[{'id':'p','version':1,'member_version':1}]
        r=personal('partner','训练搭子','林溪是训练搭子，鼓励叙述者训练。')
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':'我的训练搭子是谁？'},scopes,[r])
        previous=v2_dialogue.finish(service,plan,found,['partner'])
        plan,found,_,_=v2_dialogue.begin(service,'token',{'query':'他怎么支持我？','conversation_id':previous['id']},scopes,[])
        self.assertFalse(found)
        plan={'personal_trait':'partner','facet_ids':['partner']}
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'他怎么支持我？',[{**r,'verification':'pending'}],{'partner':'林溪鼓励我训练。'}))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'他怎么支持我？',[r],{}))

    def test_support_question_does_not_invent_missing_dates_places_or_actions(self):
        self.assertTrue(v2_intent.partner_support_followup('他怎么支持我？'))
        self.assertTrue(v2_intent.partner_support_followup('临近比赛又忙工作的时候，他怎么支持我？'))
        for q in ['昨天他怎么支持我？','在北京，他怎么支持我？','他怎么支持我，每次跑多久？','他给我用了什么药？','谁给了我帮助？']:
            self.assertFalse(v2_intent.partner_support_followup(q),q)
