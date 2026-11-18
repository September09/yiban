"""Narrative field regressions with independent evidence and account scopes."""

import json
import unittest
from yiban.memory import v2_fields, v2_narrative
from yiban.memory.v2_facts import UNKNOWN


def record(identifier, scope, title, content):
    return dict(id=identifier, scope_kind=scope, title=title, content=content,
                verification='confirmed', reference=1, fact=None)


class NarrativeFieldsTests(unittest.TestCase):
    def test_possibility_remains_possibility(self):
        row = record('travel','personal','备赛安排','出差带运动鞋，酒店附近有公园。')
        proof = '出差运动鞋成了标配，高铁上是可以完成一份报告的，酒店可以选在公园旁边。'
        bound, kind = v2_fields.bind('出差期间我的鞋和住宿如何准备？',[row],{})
        self.assertEqual(bound,row)
        result = json.loads(v2_fields.answer('高铁时间能拿来做什么？',[row],{'travel':proof},
                                             {'facet_ids':['travel'],'narrative_field':kind}))
        self.assertIn('可以完成',result['answer'])
        self.assertEqual(result['references'],[1])

    def test_gift_origin_does_not_use_meaning_shortcut(self):
        row = record('cup','team','杯子作为航海纪念','柳叶用带回的贝壳制作杯子，送给青山。')
        proof = '柳叶从海岛带回来几片贝壳。贝壳被制作成了杯子，送给青山作为同行的纪念。它提醒大家合作的价值。'
        plan = {'facet_ids':['cup']}
        question = '那件纪念物里装的材料是从哪带回的？'
        self.assertIsNone(v2_narrative.reviewed_object_meaning(question,[row],{'cup':proof},plan))
        result = json.loads(v2_fields.answer(question,[row],{'cup':proof},plan))
        self.assertEqual(result['answer'],'柳叶从海岛带回来几片贝壳。')

    def test_unknown_craft_steps_keep_grounded_what_and_where(self):
        row = record('cup','team','杯子作为航海纪念','柳叶用带回的贝壳制作杯子，送给青山。')
        proof = '柳叶从海岛带回来几片贝壳。贝壳被制作成了杯子，送给青山作为同行的纪念。'
        plan = {'facet_ids':['cup']}
        response = json.loads(v2_fields.answer('杯子是柳叶亲手做了每一步吗？',
                                               [row],{'cup':proof},plan))
        self.assertEqual(response['references'],[1])
        self.assertIn('柳叶从海岛带回来几片贝壳',response['answer'])
        self.assertIn('做成了杯子',response['answer'])
        self.assertIn('无法确认',response['answer'])
        self.assertFalse(v2_narrative.unsupported_detail(response['answer'],[row],{'cup':proof},['cup']))
        result = json.loads(v2_fields.answer('纪念物做成了什么东西？',[row],{'cup':proof},plan))
        self.assertEqual(result,{'answer':'做成了杯子。','references':[1]})

    def test_elliptic_training_field_stays_on_previous_readable_row(self):
        terrain = record('hill','personal','夜间拉练','沙地卸力，跑步吃力。')
        team = record('team','team','团队拉练','队伍跑了40公里。')
        state = {'focus':{'primary_ids':['hill']}}
        query = '跑到觉得吃力时，已经练了多远？'
        self.assertEqual(v2_fields.bind(query,[terrain,team],state),(terrain,'terrain'))
        self.assertIsNone(v2_fields.bind(query,[team],state))
        self.assertIsNone(v2_fields.bind('班级拉练跑了多远？',[terrain,team],state))
        reply = json.loads(v2_fields.answer(query,[terrain,team],{'hill':'当时已经练了接近12公里。没有其他人同行。'},
                                            {'facet_ids':['hill'],'narrative_field':'terrain'}))
        self.assertIn('接近12公里',reply['answer'])
        self.assertNotIn('40',reply['answer'])

    def test_unknown_brand_has_no_citation(self):
        row = record('night','personal','第一次心流','戴头灯夜跑。')
        plan = {'facet_ids':['night'],'narrative_field':'flow'}
        response = json.loads(v2_fields.answer('那盏灯是什么牌子？',[row],{'night':'我戴着头灯。'},plan))
        self.assertEqual(response,{'answer':UNKNOWN,'references':[]})
        self.assertIsNone(v2_fields.answer('那盏灯是什么牌子？',[],{'night':'我戴着头灯。'},plan))
