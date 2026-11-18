"""Synthetic subject shifts and training intents; no production names or answers."""
import unittest
import json
from types import SimpleNamespace
from yiban.memory import v2_dialogue, v2_intent, v2_relevance
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError
from test_v2_reasoning_guards import row, event


class DemoContextTests(unittest.TestCase):
    def session(self):
        return SimpleNamespace(dialogue=SharedDialogue(SharedError)), [{'id':'s','version':1,'member_version':1}]

    def personal(self, identity, title, content, activity=None):
        return {**row(identity,title,content,activity), 'scope_kind':'personal'}

    def test_gift_paraphrase_ranks_object_ahead_of_event_reflection(self):
        activity=event('trip','秋季出游','hiking')
        rows=[row('reflection','秋季出游感悟','分享同行与坚持。',activity),
              row('object','同行纪念','用带回的材料制成纪念杯。',activity)]
        self.assertEqual(v2_relevance.rank('秋季出游带回材料做成什么礼物？',rows,exclude=v2_relevance.terms(activity['title']))[0]['id'],'object')

    def test_elliptical_leader_question_keeps_the_cited_training(self):
        service,scopes=self.session()
        rows=[self.personal('trail','首次山间越野','青石带领第一次山间越野。'),
              self.personal('night','夜跑体验','晚上沿河跑。')]
        plan,_,_,_=v2_dialogue.begin(service,'token',{'query':'第一次山间越野走哪里？'},scopes,rows)
        plan['personal_facet']=True
        prior=v2_dialogue.finish(service,plan,rows,['trail'])
        plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':'是谁带我们跑的？','conversation_id':prior['id']},scopes,rows)
        self.assertIsNone(clarify)
        self.assertEqual([r['id'] for r in found],['trail'])

    def test_explicit_return_to_object_replaces_personal_partner(self):
        service,scopes=self.session()
        rows=[row('cup','同行纪念杯','青石制作并赠给柳叶。',event('trip','秋季出游','hiking')),
              self.personal('partner','训练搭子','林溪同频陪练。')]
        plan,_,_,_=v2_dialogue.begin(service,'token',{'query':'我的训练搭子是谁？'},scopes,rows)
        prior=v2_dialogue.finish(service,plan,rows,['partner'])
        plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':'回到那份纪念杯，送给了谁？','conversation_id':prior['id']},scopes,rows)
        self.assertIsNone(clarify)
        self.assertEqual([r['id'] for r in found],['cup'])

    def test_return_to_ambiguous_or_absent_object_does_not_pick_one(self):
        rows=[row('a','第一枚纪念章','纪念旅程。'),row('b','第二枚纪念章','纪念同行。')]
        self.assertIsNone(v2_relevance.returning_record('回到那枚纪念章，谁做的？',rows))
        self.assertIsNone(v2_relevance.returning_record('回到那条围巾，谁送的？',rows))
        self.assertIsNone(v2_relevance.returning_record('回到纪念章，分别比较两款？',rows))

    def test_time_management_intent_does_not_anchor_unrelated_personal_event(self):
        rows=[self.personal('routine','时间管理方法','出差带运动鞋，酒店靠近公园。'),
              self.personal('family','备赛期间的家庭变化','在备赛期间发生变化。')]
        for query in ['我备赛出差多，怎么挤时间训练？','我出差时怎么锻炼？']:
            self.assertEqual([r['id'] for r in v2_intent.personal_facets(query,rows)],['routine'])

    def test_training_rank_is_distinct_from_a_race_result(self):
        rows=[self.personal('rank','爬升训练榜','爬升榜第一，不等于赛事冠军。'),
              self.personal('race','比赛冲线','努力冲线。')]
        self.assertEqual([r['id'] for r in v2_intent.personal_facets('我在爬升榜第一，所以比赛是冠军吗？',rows)],['rank'])
        self.assertEqual(v2_intent.personal_facets('队友的爬升榜成绩呢？',rows),[])


    def test_reviewed_personal_extract_requires_current_proof_and_one_confirmed_row(self):
        r={**self.personal('routine','时间管理方法','出差带运动鞋，酒店靠近公园。'),'reference':1,'verification':'confirmed','fact':None}
        plan={'personal_trait':'time_management','facet_ids':['routine']}
        query='出差时怎么挤时间训练？'
        quote={'routine':'出差运动鞋是标配，酒店选公园旁边。'}
        self.assertEqual(json.loads(v2_dialogue.reviewed_personal_extract(plan,query,[r],quote))['references'],[1])
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,query,[r],{}))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,query,[{**r,'verification':'pending'}],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract({**plan,'facet_ids':['routine','other']},query,[r],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'出差时怎么挤时间训练，每次多少分钟？',[r],quote))

    def test_training_places_do_not_answer_unrecorded_distances(self):
        r={**self.personal('places','多种训练环境','地点包括森林与河边。'),'reference':1,'verification':'confirmed','fact':None}
        query='平时我在哪里训练？'
        self.assertEqual([r['id'] for r in v2_intent.personal_facets(query,[r])],['places'])
        plan={'personal_trait':'training_places','facet_ids':['places']};quotes={'places':'我跑过森林与河边。'}
        self.assertIsNotNone(v2_dialogue.reviewed_personal_extract(plan,query,[r],quotes))
        for question in ['平时我在哪里训练，每次几公里？','平时我在哪里训练，一次跑多久？']:
            self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,question,[r],quotes))

    def test_leader_extract_does_not_expand_unknown_full_name(self):
        r={**self.personal('trail','第一次越野','青队带领大家越野。'),'reference':1,'verification':'confirmed','fact':None}
        plan={'focus':{'primary_ids':['trail']}};quotes={'trail':'青队带我们跑越野。'}
        result=v2_dialogue.verified_followup(plan,'是谁带我们跑的？',[r],[r],quotes)
        self.assertEqual(json.loads(result)['answer'],r['content'])
        self.assertIsNone(v2_dialogue.verified_followup(plan,'他的全名叫什么？',[r],[r],quotes))


    def test_reflection_keeps_causal_wording_instead_of_inventing_comparison(self):
        original='我领悟到时间管理的重要，不是看到希望才坚持，而是坚持了才看到希望。'
        r={**self.personal('reflection','训练后的个人领悟','重视时间管理、目标与坚持。'),'reference':1,'verification':'confirmed','fact':None}
        query='训练后我对时间管理和坚持有什么感悟？'
        self.assertEqual([r['id'] for r in v2_intent.personal_facets(query,[r])],['reflection'])
        plan={'personal_trait':'reflection','facet_ids':['reflection']}
        self.assertIsNone(v2_dialogue.extractive_overview(plan,query,[r]))
        result=json.loads(v2_dialogue.reviewed_personal_extract(plan,query,[r],{'reflection':original}))
        self.assertIn(original,result['answer'])
        self.assertNotIn('更重要',result['answer'])
        self.assertEqual(result['references'],[1])


    def test_named_team_object_can_follow_a_personal_voice_topic(self):
        service,scopes=self.session()
        team={**row('gift','班级纪念杯','青石制作并赠给柳叶。',event('trip','秋季出游','hiking')),'scope_kind':'team','fact':None}
        personal=self.personal('partner','训练搭子','林溪同频陪练。')
        plan,_,_,_=v2_dialogue.begin(service,'token',{'query':'我的训练搭子是谁？'},scopes,[team,personal])
        prior=v2_dialogue.finish(service,plan,[personal],['partner'])
        plan,found,_,clarify=v2_dialogue.begin(service,'token',{'query':'我们班那个纪念杯是谁做的？','conversation_id':prior['id']},scopes,[team,personal])
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['gift'])


    def test_team_object_match_ignores_function_word_bigrams(self):
        activity=event('trip','秋季出游','hiking')
        gift={**row('box','木盒','用木盒收藏木片。',activity),'scope_kind':'team','fact':None}
        reflection={**row('reflection','柳叶的木艺感悟','记述工艺。',activity),'scope_kind':'team','fact':None}
        found=v2_relevance.returning_record('我们班那个木盒里的木片来自哪里？',[gift,reflection])
        self.assertEqual(found['id'],'box')


    def test_brief_places_removes_count_disclaimer_but_keeps_each_setting(self):
        row={**self.personal('places','日常训练地点','阶段回顾，地点包括森林、河边等，也利用车库和小区训练；这是阶段总结，不按地点累计活动次数。'),'reference':7,'verification':'confirmed','fact':None}
        plan={'personal_trait':'training_places','facet_ids':['places']}
        result=json.loads(v2_dialogue.reviewed_personal_extract(plan,'平时我在哪里训练？',[row],{'places':'我跑过森林、河边，还在车库和小区训练。'}))
        self.assertEqual(result['answer'],'你的训练地点包括森林、河边等，也在车库和小区练过。')
        self.assertNotIn('统计',result['answer']);self.assertNotIn('次数',result['answer'])
        self.assertEqual(result['references'],[7])

    def test_brief_reflection_selects_requested_points_and_preserves_causal_order(self):
        original='跑步是内心的对话；领悟到了时间的宝贵，时间管理的重要，要留意碎片时间；体会到了目标的力量；也明白了有时候不是看到希望才坚持，而是坚持了才看到希望…未来还有新目标。'
        row={**self.personal('reflection','个人训练感悟','重视时间管理和坚持。'),'reference':4,'verification':'confirmed','fact':None}
        plan={'personal_trait':'reflection','facet_ids':['reflection']}
        result=json.loads(v2_dialogue.reviewed_personal_extract(plan,'训练后我对时间管理和坚持有什么感悟？',[row],{'reflection':original}))
        self.assertIn('你体会到时间的宝贵，时间管理的重要，要留意碎片时间',result['answer'])
        self.assertIn('有时候不是看到希望才坚持，而是坚持了才看到希望',result['answer'])
        self.assertNotIn('未来',result['answer']);self.assertNotIn('更重要',result['answer'])
        self.assertLess(len(result['answer']),len(original))
        self.assertEqual(result['references'],[4])

    def test_brief_fallback_keeps_uncertainty_and_does_not_create_missing_cause(self):
        self.assertIsNone(v2_dialogue.brief_personal_field('training_places','地点包括森林，可能去河边训练。','计划训练。'))
        self.assertIsNone(v2_dialogue.brief_personal_field('reflection','重视坚持。','我重视时间管理；还没有想清楚坚持的意义。'))
        self.assertIsNone(v2_dialogue.brief_personal_field('reflection','重视坚持。','我懂得时间管理；别人说不是看到希望才坚持；我没有提出因果结论。'))
        row={**self.personal('reflection','感悟','重视时间管理和坚持。'),'reference':4,'verification':'confirmed','fact':None}
        plan={'personal_trait':'reflection','facet_ids':['reflection']}
        for change in ({'scope_kind':'team'},{'verification':'pending'}):
            self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'训练后时间管理和坚持有什么感悟？',[{**row,**change}],{'reflection':'时间管理与坚持。'}))

    def test_material_origin_quotes_exact_current_evidence_without_repeated_nouns(self):
        r={**row('cup','同行纪念杯','用带回的木片制作纪念杯。'),'reference':5,'verification':'confirmed','fact':None}
        plan={'focus':{'primary_ids':['cup']}}
        original='为了纪念同行，林溪从青石林带回来几片木片。这些木片被制成纪念杯。'
        result=json.loads(v2_dialogue.verified_material_origin(plan,'里面的木片从哪里来？',[r],{'cup':original}))
        self.assertEqual(result['answer'],'林溪从青石林带回来几片木片。')
        self.assertIn(result['answer'],original);self.assertEqual(result['references'],[5])
        original='林溪从银湾带回来一瓶细沙。细沙被放进纪念瓶。'
        result=json.loads(v2_dialogue.verified_material_origin(plan,'里面的沙子从哪里来？',[r],{'cup':original}))
        self.assertEqual(result['answer'],'林溪从银湾带回来一瓶细沙。')

    def test_material_origin_rejects_missing_ambiguous_unconfirmed_or_extra_detail(self):
        r={**row('cup','同行纪念杯','用木片制作纪念杯。'),'reference':5,'verification':'confirmed','fact':None}
        plan={'focus':{'primary_ids':['cup']}};quote='林溪从青石林带回来木片。'
        for question in ['里面的石头从哪里来？','里面的木片从哪里来，有几片？','里面的木片和石头从哪里来？']:
            self.assertIsNone(v2_dialogue.verified_material_origin(plan,question,[r],{'cup':quote}))
        for excerpt in ['木片来源未记载。','据说林溪从青石林带回来木片。','林溪没有从青石林带回来木片。','林溪从青石林带回来木片。柳叶从河边带回来木片。']:
            self.assertIsNone(v2_dialogue.verified_material_origin(plan,'里面的木片从哪里来？',[r],{'cup':excerpt}))
        self.assertIsNone(v2_dialogue.verified_material_origin(plan,'里面的木片从哪里来？',[{**r,'verification':'pending'}],{'cup':quote}))
        self.assertIsNone(v2_dialogue.verified_material_origin(plan,'里面的木片从哪里来？',[],{'cup':quote}))
        self.assertIsNone(v2_dialogue.verified_material_origin({'focus':{'primary_ids':['cup','other']}},'里面的木片从哪里来？',[r],{'cup':quote}))

    def test_partner_answer_uses_unique_reviewed_current_evidence(self):
        r={**self.personal('partner','训练搭子','训练搭子林溪一直鼓励，帮助叙述者继续训练。'),'reference':3,'verification':'confirmed','fact':None}
        plan={'personal_trait':'partner','facet_ids':['partner']}
        quote={'partner':'训练搭子林溪一直在坚持和鼓励，帮助我继续训练。我不是一个人在战斗。'}
        for question in ['我的训练搭子是谁，他给了我什么支持？','那我自己的训练搭子是谁？']:
            result=json.loads(v2_dialogue.reviewed_personal_extract(plan,question,[r],quote))
            self.assertEqual(result['references'],[3]);self.assertIn('林溪',result['answer'])
            self.assertIn('帮助你继续训练',result['answer']);self.assertNotIn('叙述者',result['answer'])
        for question in ['我的训练搭子是谁，他的全名是什么？','我的训练搭子是谁，他在哪工作？','我的训练搭子是谁，他跑了多久？']:
            self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,question,[r],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'我的训练搭子是谁？',[{**r,'verification':'pending'}],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'我的训练搭子是谁？',[{**r,'scope_kind':'team'}],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract({**plan,'facet_ids':['partner','other']},'我的训练搭子是谁？',[r],quote))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'我的训练搭子是谁？',[r],{}))
        self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'我的训练搭子是谁？',[{**r,'content':'搭子的姓名不详。'}],quote))
        for denied in ['训练搭子不是林溪。','林溪不是我的训练搭子。']:
            self.assertIsNone(v2_dialogue.reviewed_personal_extract(plan,'我的训练搭子是谁？',[r],{'partner':denied}))


if __name__=='__main__':unittest.main()
