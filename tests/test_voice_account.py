"""Synthetic voice/account authorization tests; no microphone or real embeddings."""
import time
import unittest
from unittest.mock import patch
import test_memory_v2 as fixture
from yiban.memory.memory_service import MemoryService
from yiban.memory.voice_account import VoiceAccount
from yiban.memory.shared_memory import SharedError
from yiban.memory import v2_query
from yiban.turns import Cancellation, Cancelled


class VoiceAccountTests(unittest.TestCase):
    setUp_fixture=fixture.V2Tests.setUp
    tearDown=fixture.V2Tests.tearDown
    publish=fixture.V2Tests.publish
    rows=fixture.V2Tests.rows

    def setUp(self):
        self.setUp_fixture()
        self.service=MemoryService(self.shared,self.model)
        self.voice=VoiceAccount(self.shared,self.service)
        self.profile=self.store.create_profile('voice-owner')['id']
        self.other=self.store.create_profile('voice-other')['id']
        self.store.enroll(self.profile,[1,0,0],'synthetic')
        self.store.enroll(self.other,[0,1,0],'synthetic')

    def fail(self,status,fn,*args,**kwargs):
        with self.assertRaises(SharedError) as caught:fn(*args,**kwargs)
        self.assertEqual(caught.exception.status,status)

    def authorize(self,token=None,profile=None):
        token=token or self.owner
        plan=self.voice.preview(token,{'profile_id':profile or self.profile})
        return self.voice.confirm(token,{'preview_token':plan['preview_token'],'confirmed':True,'consent':True})

    def receipt(self,text='第二届班长是谁？',profile=None,matched=True):
        return self.voice.receipt(text,{'matched':matched,'id':profile or self.profile})

    def test_preview_never_authorizes_and_explicit_consent_is_required(self):
        self.assertFalse(self.voice.status(self.owner,{})['authorized'])
        plan=self.voice.preview(self.owner,{'profile_id':self.profile})
        self.assertFalse(self.voice.status(self.owner,{})['authorized'])
        self.fail(400,self.voice.confirm,self.owner,{'preview_token':plan['preview_token'],'confirmed':True})
        self.assertTrue(self.authorize()['authorized'])

    def test_confirm_is_bound_to_the_exact_login_and_account(self):
        plan=self.voice.preview(self.owner,{'profile_id':self.profile})
        data={'preview_token':plan['preview_token'],'confirmed':True,'consent':True}
        self.fail(403,self.voice.confirm,self.reader,data)
        second=self.shared.login({'name':'owner','password':'v2-test-password'})['token']
        self.fail(403,self.voice.confirm,second,data)
        self.voice.confirm(self.owner,data)
        self.assertFalse(self.voice.status(second,{})['authorized'])

    def test_replay_expiry_and_changed_voiceprint_invalidate_preview(self):
        plan=self.voice.preview(self.owner,{'profile_id':self.profile})
        data={'preview_token':plan['preview_token'],'confirmed':True,'consent':True}
        self.voice.plans[plan['preview_token']]['expires']=0
        self.fail(409,self.voice.confirm,self.owner,data)
        plan=self.voice.preview(self.owner,{'profile_id':self.profile});data['preview_token']=plan['preview_token']
        self.store.enroll(self.profile,[0,0,1],'synthetic')
        self.fail(409,self.voice.confirm,self.owner,data)
        self.authorize();self.fail(409,self.voice.confirm,self.owner,{'preview_token':plan['preview_token'],'confirmed':True,'consent':True})

    def test_a_profile_cannot_be_stolen_by_another_account(self):
        self.authorize()
        self.fail(403,self.voice.preview,self.reader,{'profile_id':self.profile})
        self.assertNotIn(self.profile,[p['id'] for p in self.voice.status(self.reader,{})['profiles']])

    def test_server_receipt_queries_same_account_and_both_authorized_scopes(self):
        self.publish()
        self.publish(scope={'scope_kind':'personal'})
        self.authorize()
        result=self.voice.ask(self.owner,{'receipt_id':self.receipt()})
        self.assertIn('角色乙',result['answer'])
        self.assertEqual({r['scope_kind'] for r in result['sources']},{'personal','team'})
        self.assertEqual(result['voice_identity']['account_name'],'owner')
        self.assertTrue(result['answer_source_ids'])

    def test_identity_question_uses_verified_binding_without_reading_memory(self):
        self.authorize()
        with patch.object(self.service,'dispatch') as query:
            result=self.voice.ask(self.owner,{'receipt_id':self.receipt('我是谁？')})
            self.assertIn('voice-owner',result['answer'])
            self.assertEqual(result['answer_source_ids'],[])
            query.assert_not_called()

    def test_without_authorization_voice_match_never_queries_memory(self):
        with patch.object(self.service,'dispatch') as query:
            self.fail(403,self.voice.ask,self.owner,{'receipt_id':self.receipt()})
            query.assert_not_called()

    def test_unknown_and_different_speaker_never_query_memory(self):
        self.authorize()
        for receipt in [self.receipt(matched=False),self.receipt(profile=self.other)]:
            with patch.object(self.service,'dispatch') as query:
                self.fail(403,self.voice.ask,self.owner,{'receipt_id':receipt})
                query.assert_not_called()

    def test_client_text_profile_and_matched_flags_are_rejected(self):
        self.authorize()
        for extra in [{'query':'伪造问题'},{'profile_id':self.profile},{'matched':True},{'account_id':self.users['reader']['account']['id']}]:
            with patch.object(self.service,'dispatch') as query:
                self.fail(400,self.voice.ask,self.owner,{'receipt_id':self.receipt(),**extra})
                query.assert_not_called()

    def test_receipt_is_single_use_expiring_and_must_follow_authorization(self):
        before=self.receipt();self.authorize()
        self.fail(409,self.voice.ask,self.owner,{'receipt_id':before})
        old=self.receipt();self.voice.receipts[old]['created']=time.time()-121
        self.fail(409,self.voice.ask,self.owner,{'receipt_id':old})
        valid=self.receipt();self.voice.ask(self.owner,{'receipt_id':valid})
        self.fail(409,self.voice.ask,self.owner,{'receipt_id':valid})

    def test_logout_and_password_change_revoke_grant(self):
        self.authorize();self.shared.logout(self.owner)
        self.fail(401,self.voice.ask,self.owner,{'receipt_id':self.receipt()})
        token=self.shared.login({'name':'owner','password':'v2-test-password'})['token']
        self.assertFalse(self.voice.status(token,{})['authorized'])
        self.authorize(token)
        self.shared.change_password(token,{'old_password':'v2-test-password','new_password':'changed-test-password'})
        self.fail(401,self.voice.ask,token,{'receipt_id':self.receipt()})

    def test_reenrollment_revoke_and_unbind_stop_voice_access(self):
        self.authorize();self.store.enroll(self.profile,[0,1,1],'synthetic')
        self.fail(403,self.voice.ask,self.owner,{'receipt_id':self.receipt()})
        self.authorize();self.voice.revoke(self.owner,{})
        self.fail(403,self.voice.ask,self.owner,{'receipt_id':self.receipt()})
        self.assertIsNotNone(self.voice.status(self.owner,{})['binding'])
        self.authorize();self.voice.revoke(self.owner,{},unbind=True)
        self.assertIsNone(self.voice.status(self.owner,{})['binding'])

    def test_deleting_profile_cascades_binding_and_grants(self):
        self.authorize();self.store.delete_profile(self.profile)
        self.assertFalse(self.voice.status(self.owner,{})['authorized'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_memory_grants').fetchone()[0],0)

    def test_reenrollment_during_recognition_invalidates_receipt(self):
        old_versions=self.voice.versions()
        self.store.enroll(self.profile,[1,1,0],'synthetic')
        self.authorize()
        receipt=self.voice.receipt('读取记忆',{'matched':True,'id':self.profile},versions=old_versions)
        with patch.object(self.service,'dispatch') as query:
            self.fail(403,self.voice.ask,self.owner,{'receipt_id':receipt})
            query.assert_not_called()

    def test_cancelled_query_cannot_use_memory(self):
        self.authorize();cancel=Cancellation();cancel.cancel()
        with patch.object(self.service,'dispatch') as query:
            with self.assertRaises(Cancelled):self.voice.ask(self.owner,{'receipt_id':self.receipt()},cancel)
            query.assert_not_called()

    def test_revoke_during_generation_never_returns_the_answer(self):
        self.authorize()
        def revoke(*args,**kwargs):
            self.voice.revoke(self.owner,{})
            return {'answer':'must not escape','sources':[]}
        with patch.object(self.service,'dispatch',side_effect=revoke):
            self.fail(403,self.voice.ask,self.owner,{'receipt_id':self.receipt()})

    def test_personal_voice_authorization_does_not_grant_team_membership(self):
        outsider=self.users['outsider']['token'];self.authorize(outsider,self.other)
        self.fail(403,self.voice.ask,outsider,{'receipt_id':self.receipt(profile=self.other),'scope_kind':'team','space_id':self.space})
        scopes,rows=v2_query.snapshot(self.service,outsider,{})
        self.assertEqual(rows,[])

    def test_reader_permissions_and_other_personal_data_remain_enforced(self):
        self.publish();self.publish(scope={'scope_kind':'personal'},token=self.reader)
        self.authorize(self.reader)
        response=self.voice.ask(self.reader,{'receipt_id':self.receipt()})
        self.assertTrue(response['sources'])
        team_row=self.rows(self.reader)[0]
        self.fail(403,self.service.dispatch,'update/preview',self.reader,{**self.scope,'id':team_row['id'],'expected_version':team_row['version'],'changes':{'summary':'blocked'}})
        own_personal=self.rows(self.reader,{'scope_kind':'personal'})[0]
        self.fail(404,self.service.evidence,self.owner,{'scope_kind':'personal','id':own_personal['id']})

    def test_receipt_and_preview_stores_are_bounded(self):
        for _ in range(280):self.receipt()
        self.assertEqual(len(self.voice.receipts),256)
        for _ in range(128):self.voice.preview(self.owner,{'profile_id':self.profile})
        self.fail(429,self.voice.preview,self.owner,{'profile_id':self.profile})

    def test_missing_fields_and_unknown_routes_are_safe_errors(self):
        for data in [None,[],{'profile_id':[]}, {'profile_id':'x','account':'owner'}]:
            self.fail(400,self.voice.preview,self.owner,data)
        self.fail(400,self.voice.preview,self.owner,{})
        self.fail(404,self.voice.dispatch,'not-a-route',self.owner,{})


if __name__=='__main__':unittest.main()
