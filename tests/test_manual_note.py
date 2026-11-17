"""Literal notes use normal review and permissions; isolated data only."""
import hashlib
import unittest
import test_memory_v2 as fixture
from yiban.memory.shared_memory import SharedError

class ManualNoteTests(unittest.TestCase):
    setUp=fixture.V2Tests.setUp;tearDown=fixture.V2Tests.tearDown
    rows=fixture.V2Tests.rows
    def draft(self,token=None,scope=None,**data):
        return self.service.dispatch('draft',token or self.owner,{**(scope or {'scope_kind':'personal'}),'title':'我的茶','content':'我喜欢茉莉茶 \U0001f375。',**data})

    def test_literal_pending_unicode_evidence_and_no_writes(self):
        d=self.draft()['document'];r=d['records'][0];source=d['sources'][0]
        self.assertEqual(r['summary'],source['text'])
        self.assertEqual(r['verification'],'pending');self.assertIsNone(r['fact'])
        self.assertEqual(source['kind'],'manual');e=r['evidence'][0]
        self.assertEqual(source['text'][e['start']:e['end']],e['text'])
        self.assertEqual(e['end'],len(source['text']))
        self.assertEqual(source['sha256'],hashlib.sha256(source['text'].encode()).hexdigest())
        self.assertEqual(self.rows(scope={'scope_kind':'personal'}),[])
        self.assertEqual(self.service.plans,{})

    def test_derived_title_and_stable_key(self):
        first=self.draft(title='')['document'];second=self.draft(title='另一标题')['document']
        self.assertEqual(first['records'][0]['title'],'我喜欢茉莉茶 \U0001f375。')
        self.assertEqual(first['records'][0]['key'],second['records'][0]['key'])

    def test_bad_inputs_and_authority_fields_rejected(self):
        for data in ({'content':''},{'content':' '*8},{'content':'字'*1201},{'content':[]},{'title':'字'*161},{'verification':'confirmed'},{'fact':{'value':99}},{'owner':'other'}):
            with self.subTest(data=data),self.assertRaises(SharedError):self.draft(**data)

    def test_anonymous_rejected(self):
        with self.assertRaises(SharedError) as err:self.service.dispatch('draft',None,{'scope_kind':'personal','content':'虚构记录'})
        self.assertEqual(err.exception.status,401)

    def test_reader_and_nonmember_rejected_for_team_draft(self):
        for token in (self.reader,self.users['outsider']['token']):
            with self.assertRaises(SharedError) as err:self.draft(token,self.scope)
            self.assertEqual(err.exception.status,403)

    def test_personal_note_is_not_a_team_permission(self):
        d=self.draft(self.reader)['document']
        with self.assertRaises(SharedError):self.service.preview(self.reader,{**self.scope,'document':d,'visibility':'all'})
        self.assertEqual(self.rows(),[])

    def test_explicit_preview_confirmation_and_pending_status(self):
        scope={'scope_kind':'personal'};d=self.draft()['document']
        p=self.service.preview(self.owner,{**scope,'document':d,'visibility':'all'})
        self.assertEqual(self.rows(scope=scope),[])
        with self.assertRaises(SharedError):self.service.confirm(self.owner,{**scope,'token':p['token']})
        self.service.confirm(self.owner,{**scope,'token':p['token'],'confirmed':True,'acknowledge_warnings':True})
        records=self.rows(scope=scope);self.assertEqual(len(records),1)
        self.assertEqual(records[0]['verification'],'pending')
        self.assertEqual(self.rows(self.reader,scope),[])

if __name__=='__main__':unittest.main()
