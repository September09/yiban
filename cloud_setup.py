"""Interactive cloud setup: keys are hidden and never saved to the project."""
import argparse
import getpass
import json
import os
import ssl
import subprocess
import sys
from urllib.parse import urlsplit

from backends.models import ModelClient
from settings import ROOT, load_settings
from turns import Cancellation

TEMPLATE = ROOT / 'config/cloud-bailian.example.json'
CONFIG = ROOT / 'config/cloud.local.json'
ZHIPU_TEMPLATE = ROOT / 'config/cloud-zhipu.example.json'
ZHIPU_CONFIG = ROOT / 'config/zhipu.local.json'


def make_config(base_url, model, provider='bailian'):
    base_url, model = base_url.strip().rstrip('/'), model.strip()
    url = urlsplit(base_url)
    if provider == 'zhipu':
        allowed = url.hostname == 'open.bigmodel.cn'
        expected_path = '/api/paas/v4'
        template = ZHIPU_TEMPLATE
    elif provider == 'bailian':
        allowed = url.hostname in {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'} or (url.hostname or '').endswith('.maas.aliyuncs.com')
        expected_path = '/compatible-mode/v1'
        template = TEMPLATE
    else:
        raise ValueError('不支持的云端服务商。')
    if url.scheme != 'https' or not allowed or url.username or url.password or url.query or url.fragment or url.port not in (None, 443):
        raise ValueError('请填写所选服务商的官方 HTTPS 地址，不包含密钥、账号、查询参数或特殊端口。')
    if url.path != expected_path:
        raise ValueError(f'Base URL 应以 {expected_path} 结束，不要加 /chat/completions。')
    if '{' in base_url or '}' in base_url:
        raise ValueError('请将地址中的业务空间占位符替换成控制台提供的实际值。')
    if not model or len(model) > 160 or any(c.isspace() or ord(c) < 32 for c in model):
        raise ValueError('请输入控制台提供的模型 ID。')
    config = json.loads(template.read_text())
    config['llm'].update(base_url=base_url, model=model)
    return config


def probe(config):
    client = ModelClient(config)
    if config.get('status_check', 'models') == 'models' and not client.status()['chat']:
        raise ValueError('接口可连接，但模型列表中未找到这个模型 ID，请核对可用模型和账号权限。')
    probe_config = dict(config, max_tokens=24)
    parts = []
    complete = False
    for event in ModelClient(probe_config).chat(
            [{'role': 'user', 'content': '连接测试，请仅回复：连接成功。'}], cancel=Cancellation()):
        parts.append(event.get('message', {}).get('content', ''))
        complete = complete or bool(event.get('done'))
    if not complete or not ''.join(parts).strip():
        raise ValueError('服务没有返回完整的回答，请核对模型是否支持非思考模式。')


def failure_message(error):
    if isinstance(getattr(error, 'reason', error), ssl.SSLError):
        return '加密连接失败，请检查代理节点或网络；没有切换现有服务。'
    status = getattr(error, 'code', None)
    return {
        401: '密钥认证失败，请核对 API Key 与地域。',
        403: '账号没有调用权限，请检查业务空间、地域与模型授权。',
        404: '未找到接口，请核对 Base URL。',
        429: '服务限流或额度不足，请检查控制台。',
    }.get(status, str(error) if isinstance(error, ValueError) else '连接或测试失败，请检查网络、地址、模型权限及额度；现有服务未切换。')


def main(provider='bailian', no_open=False):
    if not sys.stdin.isatty():
        print('请在终端或通过云端启动脚本交互启动；不要将密钥放到命令行参数中。')
        return 1
    template = ZHIPU_TEMPLATE if provider == 'zhipu' else TEMPLATE
    target = ZHIPU_CONFIG if provider == 'zhipu' else CONFIG
    label = '智谱' if provider == 'zhipu' else '阿里云百炼'
    try:
        defaults = load_settings(target if target.exists() else template)['llm']
    except (ValueError, OSError):
        print(f'已有云端配置无法读取，请检查 {target.name}；现有服务未切换。')
        return 1
    print(f'接入{label}，请以自己控制台的 Base URL 和模型 ID 为准。')
    if provider == 'bailian':
        print('百炼默认示例为北京地域。')
    print(f'接入后，问题文字、近期对话、称呼和检索出的记忆将发送给{label}。原始录音、声纹特征与朗读留在本机。')
    print('本配置暂不启用看图，也不自动启用联网搜索。先进行一条短测试请求，可能产生少量 API 费用。')
    base_url = input(f"Base URL（回车使用 {defaults['base_url']}）：").strip() or defaults['base_url']
    model = input(f"模型 ID（回车使用 {defaults['model']}）：").strip() or defaults['model']
    try:
        config = make_config(base_url, model, provider)
        key_env = config['llm']['api_key_env']
        key = os.environ.get(key_env) or getpass.getpass('API Key（输入不显示，也不会保存到文件）：').strip()
        if not key or any(c.isspace() for c in key):
            raise ValueError('API Key 不能为空或含空白。')
        os.environ[key_env] = key
        # Merge defaults in memory without touching the running local configuration.
        settings = load_settings(template)
        settings['llm'].update(config['llm'])
        print('正在验证模型和真实流式回答……')
        probe(settings['llm'])
    except Exception as error:
        print(failure_message(error))
        return 1
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    print('连接测试通过。已保存接口配置（不含密钥），开始切换助手服务。')
    env = os.environ.copy()
    env['ASSISTANT_CONFIG'] = str(target)
    args = [sys.executable, str(ROOT/'launcher.py'), '--restart-web']
    if no_open:
        args.append('--no-open')
    result = subprocess.call(args, cwd=ROOT, env=env)
    if result:
        print('启动未完成。可以运行 start-local.command 恢复本地模型。')
    return result


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--provider', choices=('bailian', 'zhipu'), default='bailian')
        parser.add_argument('--no-open', action='store_true')
        args = parser.parse_args()
        raise SystemExit(main(args.provider, args.no_open))
    except (KeyboardInterrupt, EOFError):
        print('\n已取消。')
        raise SystemExit(1)
