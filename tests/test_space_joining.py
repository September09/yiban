"""Join requests tested against real SQLite, account ACLs and unified memory queries."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.memory_service import MemoryService
from yiban.memory.v2_query import snapshot, validate_snapshot
from yiban.memory.v2_schema import SCHEMA


class JoiningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=MemoryStore(Path(self.tmp.name)/'isolated.sqlite3');self.shared=SharedMemory(self.store);self.join=self.shared.joining
        self.actors={}
        for name in ('admin','applicant','other','reader'):
            self.shared.register({'name':name,'password':'test-only-password'})
            self.actors[name]=self.shared.login({'name':name,'password':'test-only-password'})
        self.owner=self.actors['admin']['token'];self.user=self.actors['applicant']['token'];self.other=self.actors['other']['token']
        self.space=self.shared.create_space(self.owner,{'name':'虚构试用小组','kind':'team'})['id']
        self.shared.set_member(self.owner,{'space_id':self.space,'name':'reader','role':'reader'})
        self.service=MemoryService(self.shared,SimpleNamespace())

    def code(self):return self.join.code_create(self.owner,{'space_id':self.space})['code']
    def apply(self, code=None, actor=None):
        actor=actor or self.user
        preview=self.join.lookup(actor,{'code':code or self.code()})
        return self.join.request(actor,{'preview_token':preview['preview_token'],'confirmed':True})
    def preview(self, request, decision='approve'):
        return self.join.review(self.owner,{'space_id':self.space,'request_id':request['request_id'],'decision':decision})
    def approve(self, request):
        plan=self.preview(request)
        return self.join.confirm(self.owner,{'preview_token':plan['preview_token'],'confirmed':True})
    def fails(self,status,fn):
        with self.assertRaises(SharedError) as caught:fn()
        self.assertEqual(caught.exception.status,status)
    def publish(self, actor,scope,key,content,visibility='selected'):
        doc={'schema':SCHEMA,'batch_name':'虚构资料','sources':[{'key':key,'name':'虚构原文','kind':'original','text':content,'sha256':hashlib.sha256(content.encode()).hexdigest()}],'entities':[],'events':[],
             'records':[{'key':key,'type':'quote','title':key,'summary':content,'evidence':[{'source_key':key,'start':0,'end':len(content),'text':content}]}]}
        plan=self.service.preview(actor,{**scope,'document':doc,'visibility':visibility,'edit_policy':'author'})
        self.service.confirm(actor,{**scope,'token':plan['token'],'confirmed':True,'acknowledge_warnings':True})
        return self.service.listing(actor,scope)['records']

    def test_code_and_pending_request_never_grant_access(self):
        code=self.code();preview=self.join.lookup(self.user,{'code':code})
        self.assertEqual(preview['space_name'],'虚构试用小组');self.assertFalse(preview['already_member'])
        self.fails(403,lambda:self.shared.members(self.user,self.space))
        self.join.request(self.user,{'preview_token':preview['preview_token'],'confirmed':True})
        self.assertEqual(self.shared.spaces(self.user)['spaces'],[])
        self.fails(403,lambda:self.service.listing(self.user,{'scope_kind':'team','space_id':self.space}))

    def test_approval_only_adds_reader_and_preserves_private_boundaries(self):
        team={'scope_kind':'team','space_id':self.space}
        self.publish(self.owner,team,'public','公开活动安排。','all');self.publish(self.owner,team,'restricted','只给管理者的安排。')
        owner_personal=self.publish(self.owner,{'scope_kind':'personal'},'owner-private','管理员私人经历。')[0]
        self.publish(self.user,{'scope_kind':'personal'},'user-private','申请人自己的经历。')
        before,_=snapshot(self.service,self.user,{})
        self.approve(self.apply())
        space=self.shared.spaces(self.user)['spaces'][0];self.assertEqual(space['role'],'reader')
        scopes,items=snapshot(self.service,self.user,{})
        self.assertEqual({r['key'] for r in items},{'public','user-private'})
        self.assertEqual({s['kind'] for s in scopes},{'personal','team'})
        self.fails(409,lambda:validate_snapshot(self.service,self.user,{},before))
        row=self.service.listing(self.user,team)['records'][0]
        self.fails(403,lambda:self.service.update_preview(self.user,{**team,'id':row['id'],'expected_version':row['version'],'changes':{'summary':'unauthorized'}}))
        self.fails(404,lambda:self.service.evidence(self.user,{'scope_kind':'personal','id':owner_personal['id'],'expected_version':owner_personal['version']}))

    def test_no_confirmation_and_wrong_actor_cannot_submit(self):
        p=self.join.lookup(self.user,{'code':self.code()})
        self.fails(400,lambda:self.join.request(self.user,{'preview_token':p['preview_token'],'confirmed':False}))
        self.fails(403,lambda:self.join.request(self.other,{'preview_token':p['preview_token'],'confirmed':True}))
        self.assertEqual(self.join.mine(self.user,{})['requests'],[])

    def test_duplicate_requests_have_one_pending_row(self):
        code=self.code();first=self.apply(code);second=self.apply(code)
        self.assertEqual(first['request_id'],second['request_id']);self.assertEqual(len(self.join.mine(self.user,{})['requests']),1)
        self.assertEqual(len(self.join.inbox(self.owner,{'space_id':self.space})['requests']),1)

    def test_only_owner_can_manage_codes_or_review(self):
        request=self.apply()
        for actor in [self.user,self.other,self.actors['reader']['token']]:
            for fn in [lambda:self.join.code_create(actor,{'space_id':self.space}),lambda:self.join.inbox(actor,{'space_id':self.space}),lambda:self.join.review(actor,{'space_id':self.space,'request_id':request['request_id'],'decision':'approve'})]:self.fails(403,fn)

    def test_preview_is_account_bound_and_consent_required(self):
        p=self.preview(self.apply())
        self.fails(400,lambda:self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':False}))
        self.fails(403,lambda:self.join.confirm(self.other,{'preview_token':p['preview_token'],'confirmed':True}))
        self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_approval_cannot_upgrade_role_from_client_payload(self):
        p=self.preview(self.apply());self.assertEqual(p['role'],'reader')
        self.fails(400,lambda:self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True,'role':'owner'}))
        self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_changed_membership_invalidates_approval_preview(self):
        request=self.apply();p=self.preview(request)
        self.shared.set_member(self.owner,{'space_id':self.space,'name':'other','role':'reader'})
        self.fails(409,lambda:self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True}))
        self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_existing_editor_is_never_downgraded(self):
        request=self.apply();self.shared.set_member(self.owner,{'space_id':self.space,'name':'applicant','role':'editor'})
        p=self.preview(request);self.assertEqual(p['role'],'editor')
        self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True})
        self.assertEqual(self.shared.spaces(self.user)['spaces'][0]['role'],'editor')

    def test_withdrawal_invalidates_pending_approval(self):
        request=self.apply();p=self.preview(request)
        self.join.cancel(self.user,{'request_id':request['request_id']})
        self.fails(409,lambda:self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True}))
        self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_rejection_grants_nothing_and_reapplication_needs_new_review(self):
        request=self.apply();p=self.preview(request,'reject')
        self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True})
        self.assertFalse(self.shared.spaces(self.user)['spaces'])
        self.assertEqual(self.join.mine(self.user,{})['requests'][0]['status'],'rejected')
        self.apply();self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_rotation_disable_and_expiry_invalidate_code(self):
        code=self.code();p=self.join.lookup(self.user,{'code':code});self.code()
        self.fails(400,lambda:self.join.lookup(self.user,{'code':code}))
        self.fails(409,lambda:self.join.request(self.user,{'preview_token':p['preview_token'],'confirmed':True}))
        code=self.code();self.join.code_disable(self.owner,{'space_id':self.space});self.fails(400,lambda:self.join.lookup(self.user,{'code':code}))
        code=self.code()
        with self.store.connect() as db:db.execute('UPDATE shared_join_codes SET expires=0')
        self.fails(400,lambda:self.join.lookup(self.user,{'code':code}))

    def test_review_replay_does_not_apply_twice(self):
        p=self.preview(self.apply());self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True})
        version=self.shared.spaces(self.owner)['spaces'][0]['version']
        self.fails(409,lambda:self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True}))
        self.assertEqual(self.shared.spaces(self.owner)['spaces'][0]['version'],version)

    def test_expired_preview_is_rejected(self):
        p=self.preview(self.apply());self.join.plans[p['preview_token']]['expires']=0
        self.fails(409,lambda:self.join.confirm(self.owner,{'preview_token':p['preview_token'],'confirmed':True}))

    def test_after_removal_approval_does_not_remain_a_membership(self):
        request=self.apply();self.approve(request)
        self.shared.set_member(self.owner,{'space_id':self.space,'account_id':self.actors['applicant']['account']['id']},remove=True)
        row=self.join.mine(self.user,{})['requests'][0];self.assertEqual(row['status'],'approved');self.assertIsNone(row['role'])
        self.apply();self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_two_previews_cannot_apply_the_same_request_twice(self):
        request=self.apply();first=self.preview(request);second=self.preview(request)
        self.join.confirm(self.owner,{'preview_token':first['preview_token'],'confirmed':True})
        self.fails(409,lambda:self.join.confirm(self.owner,{'preview_token':second['preview_token'],'confirmed':True}))
        self.assertEqual(len(self.shared.spaces(self.user)['spaces']),1)

    def test_request_code_guessing_is_rate_limited(self):
        for i in range(20):self.fails(400,lambda:self.join.lookup(self.user,{'code':'not-a-valid-request-code'}))
        self.fails(429,lambda:self.join.lookup(self.user,{'code':'not-a-valid-request-code'}))
        self.assertFalse(self.shared.spaces(self.user)['spaces'])

    def test_unknown_ids_and_malformed_fields_fail_closed(self):
        self.fails(404,lambda:self.join.cancel(self.other,{'request_id':'unknown'}))
        for data in [{'preview_token':[],'confirmed':True},{'preview_token':'x','confirmed':True,'role':'editor'}]:self.fails(400,lambda:self.join.request(self.user,data))
        self.fails(401,lambda:self.join.mine(None,{}))
        self.fails(400,lambda:self.join.lookup(self.user,{'code':'invalid-but-long-enough'}))
