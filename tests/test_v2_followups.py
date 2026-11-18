import json
import unittest

from yiban.memory import v2_dialogue, v2_facts, v2_followups


def row(key, title, body, scope='team', event=None):
    return dict(id=key,key=key,title=title,content=body,scope_kind=scope,event=event,
                verification='confirmed',reference=1,fact=None)


class FollowupBindingTests(unittest.TestCase):
    def test_alternative_day_requires_same_readable_record_and_anchored_day(self):
        itinerary = row('trip', '挑战赛的两段路线', '南山8公里，次日从东湖出发4公里。')
        proof = {'trip':'南山，正在举办越野赛，8公里赛道的草地很宽。\n次日的赛程从东湖启程，4公里都是阶梯路段。'}
        base = {'focus':{'ids':['trip'],'primary_ids':['trip']}}
        first = v2_followups.select('有阶梯的是第一天还是次日？',[itinerary],base,lambda r: proof[r['id']])
        self.assertEqual(first,('day_stairs',[itinerary],1))
        plan={'followup_fields':('day_stairs',['trip'],1)}
        self.assertIn('次日从东湖',json.loads(v2_followups.answer('第一问',[itinerary],proof,plan))['answer'])
        self.assertEqual(plan['day_anchor'],('trip',1))
        base['focus'].update(day_record='trip',day_index=1)
        out = v2_followups.select('另一天是哪儿，有多长？',[itinerary],base,lambda r: proof[r['id']])
        self.assertEqual(out,('day_alternative',[itinerary],0))
        self.assertIsNone(v2_followups.select('另一天是哪儿，有多长？',[],base,lambda r: proof[r['id']]))

    def test_score_threshold_is_not_team_score(self):
        award=row('prize','辩论展示组织奖','获得最佳组织奖。')
        old={'focus':{'ids':['prize'],'primary_ids':['prize']}}
        q='这个奖比分数线高出多少分？'
        self.assertIsNone(v2_facts.request(q,[],{'kind':'scores'}))
        self.assertEqual(v2_facts.request('篮球那场最终比分多少？',[]),{'kind':'scores'})
        self.assertEqual(v2_followups.select(q,[award],old,lambda r:'获最佳组织奖。'),('missing',[],None))
        self.assertIsNone(v2_followups.select(q,[award],old,lambda r:'分数线为80分。'))

    def test_speaker_comparison_is_bound_to_previous_excerpt(self):
        official=row('message','捐赠反馈','收到学校反馈。')
        other=row('other','无关寄语','其他人说“喜欢”。')
        proof={'message':'社区公益的赵青老师从本地传来视频。花园小学周明老师随后发来一句话：“大家很喜欢。”',
               'other':'以前有人说喜欢。'}
        prior={'focus':{'ids':['message'],'primary_ids':['message']}}
        selection=v2_followups.select('说“喜欢”的是同一个人吗？',[official,other],prior,lambda r: proof[r['id']])
        self.assertEqual(selection,('compare_speakers',[official],'喜欢'))
        plan={'followup_fields':('compare_speakers',['message'],'喜欢')}
        reply=json.loads(v2_followups.answer('',[official,other],proof,plan))
        self.assertIn('周明老师',reply['answer'])
        self.assertIn('赵青老师',reply['answer'])
        self.assertNotIn('小学周明老师',reply['answer'])

    def test_multiple_named_works_keep_both_quotes_in_query_order(self):
        event={'title':'冬日歌会决赛'}
        a=row('one','冬日歌会金奖','张平获金奖。',event=event)
        b=row('two','冬日歌会银奖','周晓获银奖。',event=event)
        b['reference']=2
        evidence={'one':'#张平**《明月白》**\n荣获金奖！','two':'#周晓** 《青竹风》**\n荣获银奖！'}
        q='冬日歌会上唱《明月白》和唱《青竹风》的分别是谁，各拿什么奖？'
        picked=v2_followups.select(q,[a,b],{},lambda r: evidence[r['id']])
        self.assertEqual(picked,('song_awards',[a,b],['明月白','青竹风']))
        plan={'followup_fields':('song_awards',['one','two'],['明月白','青竹风'])}
        self.assertEqual(v2_dialogue.expand_sources(plan,q,[a,b],[b]),[a,b])
        self.assertEqual(json.loads(v2_followups.answer(q,[a,b],evidence,plan))['references'],[1,2])
        self.assertIsNone(v2_followups.select(q,[b],{},lambda r: evidence[r['id']]))


    def test_time_options_keep_possibility_and_ignore_general_reflection(self):
        options = row('time', '时间管理方法', '用运动来减少犹豫。', scope='personal')
        reflection = row('reflection', '训练感悟', '体会到了碎片时间的宝贵。', scope='personal')
        q = '说到挤时间，我犹豫的那一会儿，本可以拿来干哪些事？'
        proof = {'time': '这段时间刚好能做一次体能，能去跑3公里，能去爬一次楼；'}
        selected = v2_followups.select(q, [options, reflection], {}, lambda r: proof[r['id']])
        self.assertEqual(selected, ('time_options', [options], None))
        plan = {'followup_fields': ('time_options', ['time'], None)}
        result = json.loads(v2_followups.answer(q, [options, reflection], proof, plan))
        self.assertEqual(result, {'answer': '那段时间本可以做一次体能、跑3公里、爬一次楼。', 'references': [1]})

    def test_two_role_labels_for_one_office_preserve_both_terms(self):
        def office(key, title, term, name):
            return dict(id=key, verification='confirmed', entities={'holder': name},
                        fact=dict(predicate='holds_role', subject_key='holder', value=title, term=term))

        first = office('first', '班务监督秘书长', '第一届', '成员甲')
        second = office('second', '班务监督委员会秘书长', '第二届', '成员乙')
        unrelated = office('other', '班长', '第二届', '成员丙')
        facts = [first, second, unrelated]
        q = '负责班务监督的秘书长，首届和第二届各是谁？'
        selector = v2_facts.request(q, facts)
        answer, sources, calculation = v2_facts.roles_answer(
            q, facts, selector['roles'], None, lambda r: '')
        self.assertEqual(sources, [first, second])
        self.assertEqual(answer, '第一届班务监督秘书长是成员甲。\n第二届班务监督委员会秘书长是成员乙。')
        self.assertEqual(calculation['kind'], 'structured_facts')
        answer, sources, calculation = v2_facts.roles_answer(
            q, [first, unrelated], selector['roles'], None, lambda r: '')
        self.assertEqual(sources, [first])
        self.assertIn(v2_facts.UNKNOWN, answer)
        self.assertEqual(calculation['missing_terms'], [2])


    def test_photo_count_and_judges_refer_to_the_same_article(self):
        finale = row('photo', '摄影比赛收到15幅投稿', '两赛道合计。')
        finale['fact'] = {'predicate': 'quantity', 'value': 15}
        scoring = row('weights', '摄影比赛评分权重', '评委评分占50%。')
        evidence = {'photo': '共收到15幅投稿作品。邀请到2019级张晓学长与2020级李平学姐担任评委。'}
        q = '班级摄影赛收到多少幅，评委是谁？'
        self.assertEqual(v2_followups.select(q, [finale, scoring], {}, lambda r: evidence[r['id']]),
                         ('photo_entries_and_judges', [finale], None))
        plan = {'followup_fields': ('photo_entries_and_judges', ['photo'], None)}
        answer = json.loads(v2_followups.answer(q, [finale, scoring], evidence, plan))
        self.assertEqual(answer, {'answer': '一共收到15幅投稿，评委是张晓和李平。', 'references': [1]})
