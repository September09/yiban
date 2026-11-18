import json
import unittest

from yiban.memory import v2_dialogue, v2_intent, v2_specifics
from yiban.memory.v2_facts import UNKNOWN


def record(identity, title, content, scope='team', fact=None, event=None):
    return {
        'id':identity, 'key':identity, 'title':title, 'content':content,
        'scope_kind':scope, 'verification':'confirmed', 'reference':1,
        'fact':fact, 'event':event,
    }


class SpecificMemoryEvidenceTests(unittest.TestCase):
    def test_cited_event_controls_host_followup(self):
        host = record('hosts', '读书会主持人', '李晓、陈佳主持开场。')
        old = record('other', '音乐会主持人', '赵青、马平主持开场。')
        private = record('partner', '我的陪练搭档', '陈乐当我的搭档。', 'personal')
        question = '那场聚会开场时，是哪两位同学搭档主持？'
        previous = {'focus':{'ids':['hosts','letter'], 'primary_ids':['letter']}}
        self.assertEqual(v2_specifics.select(question,[host,old,private],previous),(host,'host'))
        self.assertIsNone(v2_specifics.select(question,[old,private],previous))
        self.assertEqual(v2_intent.personal_facets(question,[private]),[])

    def test_narrative_statement_can_support_a_separate_attribute(self):
        row = record('mascot','海豚形象配色','海豚额头有刺绣图案。',fact={'predicate':'statement'})
        other = record('badge','校徽配色','盾牌的图案来自当地建筑。')
        question = '小海豚的额头图案从什么传统手艺得到灵感？'
        self.assertEqual(v2_specifics.select(question,[row,other],{}),(row,'motif_origin'))
        raw = v2_specifics.answer('motif_origin',[row,other],{'mascot':'额头一点鱼纹刺绣，灵感来自苏绣。'},['mascot'])
        self.assertEqual(json.loads(raw),{'answer':'鱼纹刺绣的灵感来自苏绣。','references':[1]})
        self.assertIsNone(v2_specifics.answer('motif_origin',[other],{'mascot':'额头一点鱼纹刺绣，灵感来自苏绣。'},['mascot']))

    def test_contextual_rerank_keeps_other_evidence_for_specific_field(self):
        event={'title':'图书馆吉祥物'}
        wrong = record('badge','眉心的徽章设计','这枚徽章画了云彩。',event=event)
        relevant = record('fabric','制作记录','花样取自织物图案。',event=event)
        plan={'focus':dict(event,primary_ids=['badge'])}
        query='眉心图案源自什么传统手艺？'
        actual = v2_dialogue.expand_sources(plan,query,[wrong,relevant],[relevant,wrong])
        self.assertIn(relevant, actual)
        self.assertIsNone(v2_dialogue.extractive_overview(plan,query,actual))

    def test_unknown_material_has_no_reference(self):
        row=record('costume','纪念外袍设计','绣有云纹。')
        self.assertEqual(json.loads(v2_specifics.answer('fabric',[row],
            {'costume':'外袍绣着云纹。'},['costume'])),{'answer':UNKNOWN,'references':[]})
        self.assertIsNone(v2_specifics.answer('fabric',[row],{'costume':'面料成分为100%棉。'},['costume']))

    def test_timing_clause_is_not_rewritten_as_an_action(self):
        row=record('trip','第二天出发准备','起床后装备尚未备齐。','personal')
        proof='集合时，我的必带装备还没准备好。那天低估了竞争强度，排队就开始了，还得提前热身。'
        raw=v2_specifics.answer('preparation_reason',[row],{'trip':proof},['trip'])
        answer=json.loads(raw)
        self.assertIn('必带装备还没准备好',answer['answer'])
        self.assertIn('低估了竞争强度',answer['answer'])
        self.assertNotIn('排队',answer['answer'])
        self.assertNotIn('热身',answer['answer'])
        self.assertEqual(answer['references'],[1])


    def test_project_decision_does_not_use_naming_vote(self):
        event={'title':'社团文创建设'}
        decision=record('rules','文创协作方式','重大事项全员投票。',event=event)
        naming=record('naming','文创名字','周边名字通过投票选出。',event=event)
        general=record('annual','年度共治','重大事项全员投票，财务定期公示。')
        query='做文创周边时，影响所有人的大决定怎么定下来？'
        self.assertEqual(v2_specifics.select(query,[naming,general,decision],{}),(decision,'decision_rule'))
        response=v2_specifics.answer('decision_rule',[decision],
            {'rules':'专人负责执行，重大事项全员投票，进度定期告知。'},['rules'])
        self.assertEqual(json.loads(response),{'answer':'重大事项全员投票。','references':[1]})
