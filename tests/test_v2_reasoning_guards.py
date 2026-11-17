"""Different names/values from the real evaluation; guard and context boundaries."""
import json
from types import SimpleNamespace
import unittest

from yiban.memory import v2_dialogue, v2_facts, v2_grounding, v2_relevance
from yiban.memory.shared_dialogue import SharedDialogue
from yiban.memory.shared_memory import SharedError


def row(identity, title, content, event=None):
    return {'id': identity, 'key': identity, 'scope_key': 'team:test', 'scope_name': '虚构团队',
            'title': title, 'content': content, 'fact': None, 'entities': {},
            'verification': 'confirmed', 'reference': 1,
            'event_key': event['key'] if event else None, 'event': event}


def event(key, title, kind, day='2026-09-12'):
    return {'key': key, 'title': title, 'kind': kind, 'state': 'held',
            'time': {'start': day, 'end': None, 'precision': 'day', 'basis': 'explicit'}}


class ReasoningGuardTests(unittest.TestCase):
    def test_non_count_identifiers_do_not_enter_book_arithmetic(self):
        for question in ('捐书的快递运单号码是多少？', '图书作者是谁？', '捐书礼物费用是多少？'):
            self.assertIsNone(v2_facts.request(question, []), question)
        for question in ('捐书有多少本？', '所以签收的也是345本吗？'):
            self.assertEqual(v2_facts.request(question, [])['kind'], 'quantities')

    def test_measures_and_articles_are_not_object_counts_but_gifts_still_guarded(self):
        source = row('r', '路线、比例与文章统计', '路线为8.5公里，原创29篇，投票35%。')
        for question in ('这条路线多少公里？', '原创有多少篇？', '投票占多少权重？'):
            self.assertIsNone(v2_dialogue.missing_quantity(question, [source], {'r': source['content']}))
        gift = row('g', '徽章作为同行纪念', '柳宁制作徽章，送给青禾与岚岚。')
        self.assertIsNotNone(v2_dialogue.missing_quantity('所以送了两个徽章吗？', [gift], {'g': gift['content']}))
        self.assertIsNotNone(v2_dialogue.missing_quantity('送出了几个徽章？', [gift], {'g': gift['content']}))

    def test_identifier_absence_never_uses_other_numbers_and_known_codes_are_allowed(self):
        source = row('r', '图书活动', '活动收到345本书。')
        result = json.loads(v2_grounding.identifier_missing('图书活动的运单号码是多少？', [source], {'r': source['content']}))
        self.assertEqual(result, {'answer': v2_facts.UNKNOWN, 'references': []})
        source['content'] = '快递运单号：SF836201954。'
        self.assertIsNone(v2_grounding.identifier_missing('快递运单号是什么？', [source], {'r': source['content']}))

    def test_short_distinctive_location_disambiguates_shared_college_name(self):
        topics = {
            'one': {'title': '第九届工学院青海挑战赛', 'ids': [], 'kind': 'hiking'},
            'two': {'title': '工学院田径运动会', 'ids': [], 'kind': 'competition'},
        }
        scores = v2_relevance.event_scores('去青海参加工学院挑战赛的是哪几位？', topics)
        self.assertGreater(scores['one'], scores['two'])

    def test_academic_period_does_not_anchor_an_unrelated_event(self):
        topics={
            'one':{'title':'第一次班会','ids':[],'kind':'meeting'},
            'two':{'title':'第二学年首次班级聚餐','ids':[],'kind':'meal'},
        }
        scores=v2_relevance.event_scores('第一学年发了多少篇原创、有多少转载？',topics)
        self.assertEqual(max(scores.values()),0)

    def test_event_name_retrieves_child_records_not_just_the_overview(self):
        e = event('game', '银河电竞友谊赛', 'competition')
        rows = [row('intro', '友谊赛开幕', '两支队伍参赛。', e),
                row('alpha', '飞羽队', '飞羽队队长为柳宁。', e),
                row('beta', '山海队', '山海队队长为沈溪。', e),
                row('unrelated', '第一届班长', '班长为另一位同学。')]
        found = v2_relevance.rank('银河打王者那次，各队队长是谁？', rows)
        self.assertEqual({r['id'] for r in found[:2]}, {'alpha', 'beta'})

    def test_reviewed_rank_comes_from_current_unique_outcome_and_exact_evidence(self):
        e = event('final', '虚构赛事校园总决赛', 'competition')
        bronze = row('bronze', '校级成绩', '风帆队获得校园总决赛季军。', e)
        body = v2_grounding.reviewed_rank('学校总决赛我们还是第一名吗？', [bronze], [bronze], {'bronze': bronze['content']})
        self.assertEqual(json.loads(body)['answer'], bronze['content'])
        self.assertIsNone(v2_grounding.reviewed_rank('还是第一名吗？', [bronze], [bronze], {'bronze': '比赛即将举办。'}))
        conflict = {**bronze, 'id': 'conflict', 'content': '另一报道说风帆队获得冠军。'}
        self.assertIsNone(v2_grounding.reviewed_rank('第一名吗？', [bronze, conflict], [bronze], {'bronze': bronze['content']}))
        bronze['content']='风帆队获得校园总决赛季军；与小组赛冠军不同。'
        result=v2_grounding.reviewed_rank('还是第一名吗？', [bronze], [bronze], {'bronze':'风帆队获得校园总决赛季军。'})
        self.assertEqual(json.loads(result)['answer'],'风帆队获得校园总决赛季军。')
        bronze['fact']={'predicate':'statement','subject_key':'team','value':'校园总决赛季军'}
        bronze['entities']={'team':'风帆队'}
        result=v2_grounding.reviewed_rank('我们还是第一名吗？', [bronze], [bronze], {'bronze':'风帆队获得校园总决赛季军。'})
        self.assertEqual(json.loads(result)['answer'],'风帆队：校园总决赛季军。')
        bronze['verification'] = 'pending'
        self.assertIsNone(v2_grounding.reviewed_rank('第一名吗？', [bronze], [bronze], {'bronze': bronze['content']}))

    def test_partial_attendance_is_not_full_roll_call_or_proof_of_absence(self):
        e = event('meal', '山脚小聚', 'meal')
        source = row('partial', '部分同学小聚', '有时间的同学一起小聚。', e)
        text = json.loads(v2_grounding.attendance_unknown('全班都到了吗？', [source], {'partial': source['content']}))
        self.assertEqual(text['answer'], v2_facts.UNKNOWN)
        self.assertEqual(text['references'], [1])
        self.assertIsNone(v2_grounding.attendance_unknown('全班都到了吗？', [source], {'partial': '全班全部同学到场。'}))

    def test_comparison_keeps_both_events_without_storing_their_answers(self):
        service = SimpleNamespace(dialogue=SharedDialogue(SharedError))
        scopes = [{'id': 'team:test', 'version': 1, 'member_version': 1}]
        west = event('west', '青海挑战赛', 'hiking')
        east = event('east', '北山徒步', 'hiking')
        rows = [row('west', '青海路线', '总路线8.5公里。', west),
                row('east', '北山路线', '总路线约6公里。', east)]
        plan, members, _, _ = v2_dialogue.begin(service, 'synthetic-token', {'query': '青海挑战赛路线如何？'}, scopes, rows)
        prior = v2_dialogue.finish(service, plan, members, ['west'])
        plan, members, effective, clarification = v2_dialogue.begin(service, 'synthetic-token',
            {'query': '和北山徒步相比，哪个总距离更长？', 'conversation_id': prior['id']}, scopes, rows)
        self.assertIsNone(clarification)
        self.assertEqual({r['id'] for r in members}, {'west', 'east'})
        self.assertIn('青海', effective)
        self.assertIn('北山', effective)
        self.assertNotIn('8.5', json.dumps(plan['comparison_topics'], ensure_ascii=False))

    def test_context_budget_keeps_references_aligned_and_original_evidence_intact(self):
        rows=[row(str(i),'队伍资料'+str(i),'说明。'*20) for i in range(12)]
        quotes={r['id']:'原文依据。'*100 for r in rows}
        original=dict(quotes)
        chosen,payload=v2_grounding.model_payload(rows,quotes,budget=1600)
        self.assertLess(len(chosen),len(rows))
        self.assertEqual([p['reference'] for p in payload],list(range(1,len(chosen)+1)))
        self.assertEqual(quotes,original)
        self.assertEqual(len(chosen),len(payload))

    def test_previous_facet_is_context_not_an_additional_request(self):
        service=SimpleNamespace(dialogue=SharedDialogue(SharedError))
        scopes=[{'id':'team:test','version':1,'member_version':1}]
        e=event('photo','虚构摄影活动','photography')
        rows=[row('weight','活动评分比例','评委评分占65%。',e),
              row('entries','投稿数量','共收到28幅投稿。',e)]
        plan,_,_,_=v2_dialogue.begin(service,'synthetic-token',{'query':'虚构摄影活动的评分比例呢？'},scopes,rows)
        prior=v2_dialogue.finish(service,plan,rows,['weight'])
        _,_,effective,_=v2_dialogue.begin(service,'synthetic-token',
            {'query':'最后收到多少幅投稿？','conversation_id':prior['id']},scopes,rows)
        self.assertIn('上轮条目（仅用于解析代词，不是本轮提问）：活动评分比例',effective)
        self.assertTrue(effective.endswith('本轮问题：最后收到多少幅投稿？'))
        self.assertNotIn('65%',effective)

    def test_same_day_transition_excludes_dinner_on_another_day(self):
        service = SimpleNamespace(dialogue=SharedDialogue(SharedError))
        scopes = [{'id': 'team:test', 'version': 1, 'member_version': 1}]
        visit = event('visit', '星河工厂参访', 'visit')
        dinner = event('dinner', '当日晚餐', 'meal')
        other = event('other', '另一日晚餐', 'meal', '2026-10-01')
        rows = [row('visit', '工厂参访', '参访星河工厂。', visit),
                row('dinner', '赛后晚餐', '同日晚上举行聚餐。', dinner),
                row('other', '赛后晚餐', '另一日举行聚餐。', other)]
        plan, members, _, _ = v2_dialogue.begin(service, 'synthetic-token', {'query': '星河工厂参访发生了什么？'}, scopes, rows)
        prior = v2_dialogue.finish(service, plan, members, ['visit'])
        plan, members, effective, _ = v2_dialogue.begin(service, 'synthetic-token',
            {'query': '那天晚上又一起做了什么？', 'conversation_id': prior['id']}, scopes, rows)
        self.assertEqual(plan['date_context'], '2026-09-12')
        self.assertEqual({r['id'] for r in members}, {'visit', 'dinner'})
        self.assertIn('2026-09-12', effective)


if __name__ == '__main__':
    unittest.main()
