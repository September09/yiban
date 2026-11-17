"""Read-only real-model evaluation using a separate, explicitly supplied team snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import time

from yiban.settings import ROOT
from yiban.memory.memory_store import MemoryStore
from yiban.memory.shared_memory import SharedMemory, SharedError
from yiban.memory.v2_store import StructuredMemory
from yiban.memory import v2_query, v2_dialogue
from yiban.backends.models import ModelClient


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


class ObservedModel:
    def __init__(self, config):
        self.client = ModelClient(config)
        self.calls = 0
        self.raw = ""
        self.revoke = None

    def chat(self, *args, **kwargs):
        self.calls += 1
        for event in self.client.chat(*args, **kwargs):
            self.raw += event.get("message", {}).get("content", "")
            if self.revoke:
                action, self.revoke = self.revoke, None
                action()
            yield event


def run(root):
    cases_path = root / "frozen-cases.json"
    freeze = json.loads((root / "freeze.json").read_text())
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == freeze["cases_sha256"]
    cases = json.loads(cases_path.read_text())
    document = json.loads((root / "document.json").read_text())
    config = json.loads((ROOT / "data/model-settings.json").read_text())["profiles"]["local"]
    assert config["model"] == "qwen3.5:9b" and config["backend"] == "ollama"
    assert config["base_url"].startswith("http://127.0.0.1:")
    db_path = root / "isolated.sqlite3"
    if db_path.exists():
        raise RuntimeError("Use a new evaluation directory; existing results will not be overwritten")
    store = MemoryStore(db_path)
    shared = SharedMemory(store, retriever=None)
    model = ObservedModel(config)
    service = StructuredMemory(shared, model)
    users = {}
    for name in ("评测管理员", "评测只读成员", "评测外部成员"):
        password = secrets.token_urlsafe(24)
        shared.register({"name": name, "password": password})
        users[name] = shared.login({"name": name, "password": password})
    owner = users["评测管理员"]["token"]
    reader = users["评测只读成员"]["token"]
    outsider = users["评测外部成员"]["token"]
    reader_id = users["评测只读成员"]["account"]["id"]
    space = shared.create_space(owner, {"name": "25A班共同记忆", "kind": "team"})["id"]
    shared.set_member(owner, {"space_id": space, "name": "评测只读成员", "role": "reader"})
    scope = {"scope_kind": "team", "space_id": space}
    # Production import uses multiple packages (schema limit: 20 sources).
    batches, records, source_keys = [], [], set()
    for record in document["records"]:
        needed = {e["source_key"] for e in record["evidence"]}
        if records and len(source_keys | needed) > 20:
            batches.append((records, source_keys))
            records, source_keys = [], set()
        records.append(record)
        source_keys |= needed
    if records:
        batches.append((records, source_keys))
    for records, keys in batches:
        package = {**document, "records": records, "sources": [s for s in document["sources"] if s["key"] in keys]}
        plan = service.preview(owner, {**scope, "document": package, "visibility": "all", "edit_policy": "author"})
        service.confirm(owner, {**scope, "token": plan["token"], "confirmed": True, "acknowledge_warnings": True})
    rows = service.listing(owner, scope)["records"]
    by_key = {r["key"]: r for r in rows}
    before = {r["id"]: (r["version"], r["content"], r["verification"]) for r in rows}
    observed = {}
    original_begin = v2_dialogue.begin

    def observe_begin(*args, **kwargs):
        result = original_begin(*args, **kwargs)
        plan, items, effective, clarification = result
        observed.update(
            effective_query=effective, intent=plan.get("request"),
            focus=(plan.get("focus") or {}).get("title"),
            post_topic_record_keys=[r["key"] for r in items], clarification=clarification)
        return result

    v2_dialogue.begin = observe_begin
    conversation = None
    previous_chain = None
    results = []
    try:
        for case in cases:
            observed.clear()
            if case["chain"] != previous_chain:
                conversation = None
            previous_chain = case["chain"]
            model.raw = ""
            start_calls = model.calls
            started = time.monotonic()
            result = {"id": case["id"], "question": case["question"], "kind": case["kind"]}
            token = reader if case["kind"] in ("reader", "reader_edit", "revoke") else owner
            if case["kind"] == "anonymous":
                token = None
            elif case["kind"] == "outsider":
                token = outsider
            query = case["question"]
            if case["kind"] == "anonymous":
                query = "第二次集体生日会是什么时候，有多少位同学？"
            elif case["kind"] == "outsider":
                query = "摄影比赛有哪两个赛道？"
            elif case["kind"] == "reader":
                query = "第二次集体庆生是什么时候，给哪几个月生日的同学办的，有多少位？"
            elif case["kind"] == "revoke":
                query = "菲尼克斯参访中，易书波如何解释信任与责任？"
                model.revoke = lambda: shared.set_member(owner, {"space_id": space, "account_id": reader_id}, True)
            try:
                if case["kind"] == "reader_edit":
                    target = by_key[next(c for c in cases if c["id"] == "N16")["gold"][0]["key"]]
                    response = service.update_preview(token, {**scope, "id": target["id"], "expected_version": target["version"], "changes": {"summary": "未经授权的改写测试"}})
                else:
                    data = {**scope, "query": query}
                    if conversation:
                        data["conversation_id"] = conversation
                    response = v2_query.answer(service, token, data)
                conversation = response.get("conversation", {}).get("id")
                ids = set(response.get("answer_source_ids", []))
                result.update(
                    answer=response.get("answer", ""), calculation=response.get("calculation"),
                    conversation=response.get("conversation"), agent=response.get("agent"), query_route=response.get("query_route"), answer_method=response.get("answer_method"), sources=[
                        {"key": r["key"], "title": r["title"], "event": r.get("event"),
                         "cited": r["id"] in ids, "content": r["content"],
                         "evidence": service.evidence(owner, {**scope, "id": r["id"], "expected_version": r["version"]}).get("quotes", [])}
                        for r in response.get("sources", [])],
                    returned_plan="token" in response)
            except SharedError as error:
                result.update(error=str(error), status=error.status)
                conversation = None
            except Exception as error:
                result.update(error=type(error).__name__ + ": " + str(error), status=500)
                conversation = None
            result.update(
                seconds=round(time.monotonic()-started, 3),
                model_calls=model.calls-start_calls, model_raw=model.raw,
                diagnostic=dict(observed))
            results.append(result)
            (root / ("result-" + case["id"] + ".json")).write_text(encoded(result))
            (root / "results.json").write_text(encoded(results))
            print(encoded({"id": result["id"], "seconds": result["seconds"], "model_calls": result["model_calls"],
                           "answer": result.get("answer"), "error": result.get("error")}), flush=True)
            model.revoke = None
    finally:
        v2_dialogue.begin = original_begin
    rows = service.listing(owner, scope)["records"]
    after = {r["id"]: (r["version"], r["content"], r["verification"]) for r in rows}
    assert before == after
    summary = {"cases": len(results), "model_calls": model.calls, "records_unchanged": len(before),
               "config": {k: config[k] for k in ("backend", "model", "max_tokens", "temperature", "think") if k in config},
               "retrieval": "structured + keyword/topic; vector disabled",
               "speech": False, "frozen_cases_sha256": freeze["cases_sha256"]}
    (root / "run-summary.json").write_text(encoded(summary))
    print(encoded(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    run(parser.parse_args().directory.resolve())
