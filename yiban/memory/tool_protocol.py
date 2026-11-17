"""Shared bounded JSON transport for model-selected tools, independent of memory versions."""
from yiban.conversation_diagnostics import model_events


class ToolProtocolError(ValueError):
    pass


def collect_json(models,messages,cancel,schema,max_chars=6000):
    raw='';done=False
    events=model_events(models.chat(messages,cancel=cancel,json_schema=schema))
    try:
        for event in events:
            cancel.check()
            if event.get('error'):raise ToolProtocolError('模型工具选择失败。')
            raw+=event.get('message',{}).get('content','')
            done=done or bool(event.get('done'))
            if len(raw)>max_chars:raise ToolProtocolError('模型返回过长。')
    finally:
        events.close()
    if not done or not raw.strip():raise ToolProtocolError('模型未完成工具选择。')
    return raw
