"""Novel relation/description cases use synthetic objects and no demo answers."""
import json
import unittest
from types import SimpleNamespace
from yiban.memory import v2_dialogue,v2_intent,v2_narrative,v2_grounding,v2_relevance
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError
from test_v2_paraphrase_repair import personal
from test_v2_reasoning_guards import row,event


class UnseenRepairTests(unittest.TestCase):
    def seed(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError))
        scopes=[dict(id='s',version=1,member_version=1)]
        cup=dict(row('gift','玻璃瓶作为航海纪念','青岚用带回的海水制作玻璃瓶，赠给松石和柳叶。',event('sail','夏季航行','other')),scope_kind='team',space_id='s',version=1)
        friend=personal('friend','训练搭子','训练搭子山青鼓励叙述者在繁忙时坚持。')
        return service,scopes,cup,friend

    def test_paraphrased_personal_target_and_fields(self):
        self.assertEqual(v2_intent.scope_target('先聊我个人，当初是什么打动了我？'),'personal')
        rows=[personal('join','加入俱乐部起因','看到宣传后加入俱乐部。'),personal('place','日常训练环境','地点包括林间与车库。'),personal('health','训练中受伤','去诊所放松。')]
        self.assertEqual([r['id'] for r in v2_intent.personal_facets('我当初被什么打动才踏上越野之路？',rows)],['join'])
        self.assertEqual([r['id'] for r in v2_intent.personal_facets('我的训练还在哪些日常场所跑过？',rows)],['place'])
        self.assertFalse(v2_intent.personal_facets('团队训练的不同场所分别是什么？',rows))

    def test_descriptive_original_rescue_is_bounded_to_authorized_rows(self):
        _,_,cup,_=self.seed()
        other=dict(cup,id='other',title='水杯纪念',content='带回纪念水杯。')
        texts={'gift':'为纪念远航，瓶中保留海水和星光倒影。','other':'水杯用于喝水。'}
        called=[]
        def evidence(token,data):
            called.append(data['id'])
            return {'quotes':[{'text':texts[data['id']]}]}
        service=SimpleNamespace(evidence=evidence)
        self.assertEqual(v2_narrative.descriptive_object(service,'t','留住星光倒影的纪念物是什么？',[cup,other])['id'],'gift')
        self.assertEqual(set(called),{'gift','other'})
        self.assertIsNone(v2_narrative.descriptive_object(service,'t','留住星光倒影的纪念物是什么？',[other]))
        self.assertIsNone(v2_narrative.descriptive_object(service,'t','这份礼物是什么？',[cup,other]))

    def test_original_description_precedes_generic_summary_shortcut(self):
        service,scopes,cup,_=self.seed()
        other=dict(cup,id='other',title='航行纪念品',content='赠送纪念水杯。')
        service.evidence=lambda token,data: {'quotes':[{'text':'瓶中保留星光倒影，回想航行。' if data['id']=='gift' else '赠送水杯。'}]}
        plan,found,_,clarify=v2_dialogue.begin(service,'t',{'query':'我们班把星光倒影留在哪件纪念品里？'},scopes,[cup,other])
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['gift'])

    def test_multiple_matching_descriptions_never_choose_arbitrarily(self):
        _,_,cup,_=self.seed()
        duplicate=dict(cup,id='duplicate')
        service=SimpleNamespace(evidence=lambda *a:{'quotes':[{'text':'瓶中保留海水和星光倒影。'}]})
        self.assertIsNone(v2_narrative.descriptive_object(service,'t','星光倒影的纪念物？',[cup,duplicate]))

    def test_recent_team_object_survives_personal_topic_and_unknown(self):
        service,scopes,cup,friend=self.seed()
        plan,found,_,_=v2_dialogue.begin(service,'t',{'query':'班里玻璃瓶是谁准备的？'},scopes,[cup,friend])
        prior=v2_dialogue.finish(service,plan,[cup],['gift'])
        plan,found,_,_=v2_dialogue.begin(service,'t',{'query':'我的训练搭子是谁？','conversation_id':prior['id']},scopes,[cup,friend])
        prior=v2_dialogue.finish(service,plan,[friend],['friend'])
        plan,found,_,clarify=v2_dialogue.begin(service,'t',{'query':'现在切回团队，那件纪念物是什么意义？','conversation_id':prior['id']},scopes,[cup])
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['gift'])
        prior=v2_dialogue.finish(service,plan,[cup],[])
        plan,found,_,_=v2_dialogue.begin(service,'t',{'query':'那两位以外的人都没收到吗？','conversation_id':prior['id']},scopes,[cup])
        self.assertEqual([r['id'] for r in found],['gift'])

    def test_return_never_rehydrates_unreadable_objects_or_other_named_activity(self):
        service,scopes,cup,friend=self.seed()
        previous={'recent':[v2_intent.record_topic(cup)]}
        self.assertIsNone(v2_narrative.returned_object('切回团队，那件纪念物呢？',[friend],previous))
        self.assertIsNone(v2_narrative.returned_object('切回团队，生日礼物是谁做的？',[cup],previous))
        self.assertEqual(v2_relevance.returning_record('班里玻璃瓶又是谁准备的？',[cup])['id'],'gift')

    def test_new_descriptive_object_cannot_hijack_recent_gift(self):
        service,_,cup,_=self.seed()
        service.evidence=lambda *args: {'quotes':[{'text':'玻璃瓶里装着海水，赠给松石。'}]}
        prev={'recent':[v2_intent.record_topic(cup)]}
        self.assertIsNone(v2_narrative.returned_object('切回团队，那件羊毛的纪念物呢？',[cup],prev,service,'t'))
        self.assertEqual(v2_narrative.returned_object('切回团队，那件玻璃里的纪念物呢？',[cup],prev,service,'t')['id'],'gift')

    def test_recipient_names_do_not_imply_count_or_exhaustive_list(self):
        _,_,cup,_=self.seed();proof={'gift':'海水被制作成玻璃瓶，送给松石和柳叶作为纪念。'}
        plan={'focus':{'primary_ids':['gift']}}
        result=json.loads(v2_grounding.item_field_unknown('所以一共做了两只，对吗？',[cup],proof,plan))
        self.assertEqual(result['references'],[])
        for query in ('送给山青的吗？','其他人肯定没收到，对吗？'):
            result=json.loads(v2_narrative.gift_boundary(query,[cup],proof,plan))
            self.assertIn('松石和柳叶',result['answer']);self.assertIn('无法确认',result['answer'])
            self.assertNotIn('其他人肯定没收到',result['answer']);self.assertEqual(result['references'],[1])
        self.assertIsNone(v2_narrative.gift_boundary('其他人没收到吗？',[dict(cup,verification='pending')],proof,plan))

    def test_new_actions_and_quantities_need_original_support(self):
        _,_,cup,_=self.seed();proof={'gift':'海水被制成玻璃瓶。'}
        for answer in ('青岚亲手制作玻璃瓶。','一共制作了一个玻璃瓶和两只纪念瓶。'):
            self.assertTrue(v2_narrative.unsupported_detail(answer,[cup],proof,['gift']))
        self.assertFalse(v2_narrative.unsupported_detail('玻璃瓶用于纪念航行。',[cup],proof,['gift']))
        self.assertFalse(v2_narrative.unsupported_detail('青岚亲手制作玻璃瓶。',[cup],{'gift':'青岚亲手制作玻璃瓶。'},['gift']))

    def test_plural_location_anaphora_keeps_current_private_field_and_absent_distance(self):
        service,scopes,_,_=self.seed()
        places=personal('places','日常训练地点','地点包括林间与车库。')
        plan,found,_,_=v2_dialogue.begin(service,'t',{'query':'我的日常训练地点在哪？'},scopes,[places])
        prior=v2_dialogue.finish(service,plan,[places],['places'])
        q='这些地方我分别跑了多少公里？'
        plan,found,_,clarify=v2_dialogue.begin(service,'t',{'query':q,'conversation_id':prior['id']},scopes,[places])
        self.assertIsNone(clarify);self.assertEqual([r['id'] for r in found],['places'])
        result=json.loads(v2_grounding.item_field_unknown(q,found,{'places':'在林间与车库跑步。'},plan))
        self.assertEqual(result['references'],[])

    def test_multi_field_question_never_replaced_with_a_short_overview(self):
        _,_,cup,_=self.seed()
        self.assertIsNone(v2_dialogue.extractive_overview({'facet_ids':['gift']},'材料来源和受赠者分别是什么？',[cup]))

    def test_reflective_time_question_uses_original_clause_not_a_new_action(self):
        r=personal('reflection','训练感悟','重视时间管理、目标和坚持。')
        original='领悟到时间管理的重要，留意碎片时间；也明白有时候不是看到希望才坚持，而是坚持了才看到希望。'
        result=v2_dialogue.brief_personal_field('reflection',r['content'],original,'跑步让我悟到了怎样对待碎片时间？')
        self.assertIn('留意碎片时间',result);self.assertNotIn('创作',result);self.assertNotIn('坚持',result)
        self.assertEqual([x['id'] for x in v2_intent.personal_facets('从跑步里悟到了怎样对待碎片时间？',[r])],['reflection'])

    def test_two_object_fields_come_from_the_same_reviewed_original(self):
        _,_,cup,_=self.seed()
        proof={'gift':'为了留存远航，青岚从银湾带回来一瓶海水。海水制成纪念瓶，送给松石和柳叶作为纪念。'}
        plan={'facet_ids':['gift']}
        q='材料的来处和两位收礼人分别是什么？'
        result=json.loads(v2_narrative.origin_and_recipient(q,[cup],proof,plan))
        self.assertIn('青岚从银湾带回来一瓶海水',result['answer'])
        self.assertIn('送给松石和柳叶',result['answer']);self.assertEqual(result['references'],[1])
        self.assertIsNone(v2_narrative.origin_and_recipient(q,[dict(cup,verification='pending')],proof,plan))
        self.assertIsNone(v2_narrative.origin_and_recipient(q,[],proof,plan))
        self.assertIsNone(v2_narrative.origin_and_recipient(q+'还花了多少费用？',[cup],proof,plan))
        self.assertIsNone(v2_narrative.origin_and_recipient(q,[cup],{'gift':'收礼人与来处未知。'},plan))
        self.assertIsNone(v2_narrative.origin_and_recipient(q,[cup],{'gift':proof['gift']+'山青从河边带回石子。'},plan))

    def test_object_meaning_and_identity_use_actual_purpose_not_a_model_negative(self):
        _,_,cup,_=self.seed()
        plan={'facet_ids':['gift']}
        original='为了留存远航伙伴的情谊，青岚带回海水。海水制成玻璃瓶，送给松石和柳叶。这是集体记忆的见证。'
        result=json.loads(v2_narrative.reviewed_object_meaning('切回团队，纪念物想纪念的只是海景吗？',[cup],{'gift':original},plan))
        self.assertIn('情谊',result['answer']);self.assertIn('集体记忆',result['answer'])
        self.assertEqual(result['references'],[1])
        self.assertIsNone(v2_narrative.reviewed_object_meaning('这份纪念物花了多少元？',[cup],{'gift':original},plan))
        self.assertIsNone(v2_narrative.reviewed_object_meaning('纪念物有什么意义？',[dict(cup,verification='pending')],{'gift':original},plan))
        self.assertIsNone(v2_narrative.reviewed_object_meaning('纪念物有什么意义？',[],{'gift':original},plan))

    def test_reference_pruning_keeps_both_independent_relations(self):
        _,_,cup,friend=self.seed()
        sources=[cup,friend];proof={'gift':'青岚赠送纪念瓶。','friend':'山青持续鼓励训练。'}
        self.assertEqual(set(v2_narrative.prune_references('青岚赠送纪念瓶；山青持续鼓励训练。',sources,proof,['gift','friend'])),{'gift','friend'})
        unrelated=dict(friend,id='other',content='明天去远足。')
        self.assertEqual(v2_narrative.prune_references('青岚赠送纪念瓶。',[cup,unrelated],{**proof,'other':'明天去远足。'},['gift','other']),['gift'])
