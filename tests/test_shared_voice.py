"""Speech workflow tests use server-issued ASR receipts, temporary DBs and no real people."""
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_shared_memory as fixture
from yiban.memory.shared_voice import SharedVoice
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation, Cancelled


class SharedVoiceTests(unittest.TestCase):
    tearDown=fixture.SharedMemoryTests.tearDown
    token=fixture.SharedMemoryTests.token
    uid=fixture.SharedMemoryTests.uid
    member=fixture.SharedMemoryTests.member
    fail=fixture.SharedMemoryTests.fail
    payload=fixture.SharedMemoryTests.payload
    publish=fixture.SharedMemoryTests.publish

    def setUp(self):
        fixture.SharedMemoryTests.setUp(self)
        self.voice=SharedVoice(self.shared)
        self.profile=self.store.create_profile('voice-test')['id']
        self.other=self.store.create_profile('different-voice')['id']
        self.store.enroll(self.profile,[1,0,0],'test')
        self.session=self.voice.start(self.token('owner'),{'space_id':self.space,'profile_id':self.profile})['session']

    def utter(self,text,profile=None,session=None,token=None):
        receipt=self.voice.receipt(text,{'matched':True,'id':profile or self.profile})
        return self.voice.process(token or self.token('owner'),{'session':session or self.session,'utterance_id':receipt},Cancellation())

    def played(self,result,session=None,token=None):
        return self.voice.played(token or self.token('owner'),{'session':session or self.session,'review_id':result['review_id']})

    def test_new_memory_waits_for_complete_playback_and_precise_confirmation(self):
        plan=self.utter('你好小伴，记住：周日下午三点去公园')
        self.assertTrue(plan['needs_confirmation']);self.assertFalse(plan['saved'])
        self.assertIn('测试家庭',plan['reply']);self.assertIn('全体成员',plan['reply']);self.assertIn('仅作者和管理员',plan['reply'])
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])
        self.assertFalse(self.utter('确认保存')['saved'])
        self.played(plan)
        self.assertFalse(self.utter('好的')['saved'])
        saved=self.utter('确认保存')
        self.assertTrue(saved['saved'])
        self.assertFalse(self.utter('确认保存')['saved'])
        item=self.shared.memories(self.token('owner'),self.space)['memories'][0]
        self.assertEqual(item['author'],self.uid('owner'));self.assertEqual(item['source_note'],'语音确认')

    def test_receipt_before_playback_cannot_confirm_and_replay_rejected(self):
        plan=self.utter('记住：新内容')
        receipt=self.voice.receipt('确认保存',{'matched':True,'id':self.profile})
        self.played(plan)
        data={'session':self.session,'utterance_id':receipt}
        self.assertFalse(self.voice.process(self.token('owner'),data,Cancellation())['saved'])
        self.fail(409,self.voice.process,self.token('owner'),data,Cancellation())
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_client_text_and_client_identity_cannot_replace_audio(self):
        self.fail(409,self.voice.process,self.token('owner'),{'session':self.session,'text':'确认保存','speaker':{'matched':True,'id':self.profile}},Cancellation())
        receipt=self.voice.receipt('记住：假冒内容',{'matched':False})
        self.fail(403,self.voice.process,self.token('owner'),{'session':self.session,'utterance_id':receipt,'profile_id':self.profile},Cancellation())
        self.assertNotIn(self.session,self.voice.sessions)

    def test_account_binding_logout_and_voiceprint_change(self):
        receipt=self.voice.receipt('记住：test',{'matched':True,'id':self.profile})
        self.fail(403,self.voice.process,self.token('editor'),{'session':self.session,'utterance_id':receipt},Cancellation())
        self.store.enroll(self.profile,[0,1,0],'test')
        self.fail(403,self.utter,'查询家庭记忆')
        self.session=self.voice.start(self.token('owner'),{'space_id':self.space,'profile_id':self.profile})['session']
        self.shared.logout(self.token('owner'))
        self.fail(401,self.utter,'查询家庭记忆')

    def test_other_speaker_locks_without_write(self):
        plan=self.utter('记住：安排')
        self.played(plan)
        self.fail(403,self.utter,'确认保存',self.other)
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_cancel_interrupt_timeout_and_new_question_clear_draft(self):
        for action in ('cancel','interrupt','timeout','question'):
            plan=self.utter('记住：待取消的内容');self.played(plan)
            if action=='cancel':self.utter('取消')
            if action=='interrupt':self.voice.cancel(self.token('owner'),{'session':self.session})
            if action=='timeout':self.voice.sessions[self.session]['pending']['expires']=time.monotonic()-1
            if action=='question':self.assertIn('query',self.utter('周末有什么安排'))
            self.assertFalse(self.utter('确认保存')['saved'])
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_visibility_change_requires_new_playback(self):
        plan=self.utter('记住：活动安排');self.played(plan)
        scoped=self.utter('仅让reader可见')
        self.assertNotEqual(plan['review_id'],scoped['review_id'])
        self.assertIn('reader',scoped['reply']);self.assertIn('owner',scoped['reply'])
        self.fail(409,self.played,plan)
        self.assertFalse(self.utter('确认保存')['saved'])
        self.played(scoped);self.assertTrue(self.utter('确认保存')['saved'])
        item=self.shared.memories(self.token('owner'),self.space)['memories'][0]
        self.assertEqual(item['visibility'],'selected');self.assertEqual(item['viewers'],[self.uid('reader')])

    def test_edit_unique_match_preserves_author_and_scope(self):
        item=self.publish('editor',content='周末活动：周日散步',edit_policy='editors')
        plan=self.utter('把周末活动改成周末活动：周六去公园')
        self.assertIn('准备修改',plan['reply']);self.played(plan)
        self.assertFalse(self.utter('确认保存')['saved'])
        self.assertTrue(self.utter('确认修改')['saved'])
        after=self.shared.memories(self.token('owner'),self.space)['memories'][0]
        self.assertEqual(after['id'],item['id']);self.assertEqual(after['author'],self.uid('editor'));self.assertEqual(after['editor'],self.uid('owner'))
        self.assertEqual(self.shared.history(self.token('owner'),{'space_id':self.space,'id':item['id']})['total'],2)

    def test_ambiguous_and_nonexistent_edit_never_writes(self):
        self.publish(content='周末活动：散步');self.publish(content='周末活动：跑步')
        for text in ('把周末活动改成周六休息','把不存在的内容改成其他'):
            result=self.utter(text);self.assertFalse(result['saved']);self.assertNotIn('review_id',result)

    def test_public_mode_filters_selected_memories(self):
        self.publish(content='公开记录')
        self.publish(content='仅作者秘密',visibility='selected')
        query=self.utter('有哪些家庭记录')
        self.assertTrue(query['public_only'])
        context=self.shared.question_context(self.token('owner'),self.space,query['query'],public_only=True)
        self.assertNotIn('仅作者秘密',context['context'])
        self.assertFalse(self.utter('把仅作者秘密改成泄露')['saved'])
        private=self.voice.start(self.token('owner'),{'space_id':self.space,'profile_id':self.profile,'private_listening':True})['session']
        query=self.utter('有哪些记录',session=private)
        self.assertFalse(query['public_only'])

    def test_membership_changes_and_database_changes_invalidate_confirmation(self):
        token=self.token('editor')
        session=self.voice.start(token,{'space_id':self.space,'profile_id':self.profile})['session']
        plan=self.utter('记住：成员草稿',session=session,token=token);self.played(plan,session,token)
        self.member('editor','reader')
        self.fail(403,self.utter,'确认保存',None,session,token)
        plan=self.utter('记住：管理员草稿');self.played(plan)
        self.publish(content='其他窗口刚保存')
        self.fail(409,self.utter,'确认保存')

    def test_possible_conflicts_require_explicit_conflict_confirmation(self):
        self.publish(content='我喜欢咖啡')
        plan=self.utter('记住：我不喜欢咖啡');self.played(plan)
        self.assertIn('可能冲突',plan['reply'])
        self.assertFalse(self.utter('确认保存')['saved'])
        self.assertTrue(self.utter('确认保存并保留冲突')['saved'])
        self.assertEqual(len(self.shared.memories(self.token('owner'),self.space)['memories']),2)

    def test_role_management_unknown_intents_and_cross_scope_are_not_executed(self):
        for text in ('删除所有记忆','添加成员甲','修改权限','保存到团队共同记忆：其他团队内容','不要记住这件事'):
            result=self.utter(text);self.assertFalse(result['saved']);self.assertNotIn('query',result)
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_cancelled_turn_cannot_create_review_or_save(self):
        cancelled=Cancellation();cancelled.cancel()
        receipt=self.voice.receipt('记住：不应保存',{'matched':True,'id':self.profile})
        with self.assertRaises(Cancelled):self.voice.process(self.token('owner'),{'session':self.session,'utterance_id':receipt},cancelled)
        self.assertIsNone(self.voice.sessions[self.session]['pending'])

    def test_short_unmatched_cancel_can_only_discard_draft(self):
        plan=self.utter('记住：待取消内容');self.played(plan)
        receipt=self.voice.receipt('取消',{'matched':False})
        result=self.voice.process(self.token('owner'),{'session':self.session,'utterance_id':receipt},Cancellation())
        self.assertFalse(result['saved']);self.assertIsNone(self.voice.sessions[self.session]['pending'])
        self.assertEqual(self.shared.memories(self.token('owner'),self.space)['memories'],[])

    def test_invalid_scope_request_discards_previous_confirmation(self):
        plan=self.utter('记住：原草稿');self.played(plan)
        self.fail(400,self.utter,'仅让不存在的成员可见')
        self.assertFalse(self.utter('确认保存')['saved'])

    def test_query_guard_fails_after_cancel(self):
        query=self.utter('家庭安排是什么')
        self.voice.cancel(self.token('owner'),{'session':self.session})
        self.fail(409,self.voice.validate_query,self.token('owner'),{'session':self.session},query['generation'])
