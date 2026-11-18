"""One account-scoped memory application service for every interactive entry point.

The structured tables remain unchanged: this facade removes product version
selection, not source evidence or record history. Legacy profile/shared notes
are archive data and can never be selected by a model/version flag.
"""
import copy
import json
from yiban.memory.v2_store import StructuredMemory
from yiban.memory import v2_query, v2_relevance
from yiban.memory.shared_memory import SharedError
from yiban.turns import Cancellation


class MemoryService(StructuredMemory):
    def dispatch(self, path, token, data, cancel=None, context=''):
        if not isinstance(data, dict):
            raise SharedError('请求格式不正确。')
        data = {k: v for k, v in data.items() if k not in ('memory_version', 'profile_id')}
        return super().dispatch(path, token, data, cancel, context)

    def question_context(self, token, space_id, query, public_only=False, cancel=None, **ignored):
        """Model comparison uses the same ACL snapshot and exact evidence reads."""
        cancel = cancel or Cancellation()
        data = {'scope_kind': 'team', 'space_id': space_id}
        scopes, rows = v2_query.snapshot(self, token, data)
        if public_only:
            rows = [r for r in rows if r['visibility'] == 'all']
        sources = v2_relevance.rank(query, rows)[:6]
        payload = []
        for i, row in enumerate(sources, 1):
            cancel.check()
            row['reference'] = i
            row['memory_version'] = 2
            row['record_id'] = row['id']
            evidence = self.evidence(token, {**data, 'id': row['id'], 'expected_version': row['version']})
            payload.append({'reference': i, 'content': row['content'], 'status': row['status'],
                            'original_excerpt': '\n'.join(q['text'] for q in evidence['quotes'])})
        guard = {'data': data, 'scopes': copy.deepcopy(scopes)}
        self.validate_answer(token, guard)
        return {'guard': guard, 'sources': sources, 'space_id': space_id,
                'space_name': scopes[0]['name'], 'context': json.dumps(payload, ensure_ascii=False)}

    def validate_answer(self, token, guard):
        v2_query.validate_snapshot(self, token, guard['data'], guard['scopes'])
