"""Start/stop this demo without changing system startup settings."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from yiban.settings import load_settings

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
RUNTIME.mkdir(exist_ok=True)
STATE = RUNTIME / "processes.json"


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.load(response)
    except Exception:
        return None


def start(name, args, env=None):
    with (RUNTIME / (name + ".log")).open("a") as log:
        child = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
    return child.pid


def main():
    settings = load_settings()
    if os.environ.get("ASSISTANT_CONFIG"):
        os.environ["ASSISTANT_CONFIG"] = str(Path(os.environ["ASSISTANT_CONFIG"]).expanduser().resolve())
    page_url = f"http://127.0.0.1:{settings['port']}"
    processes = json.loads(STATE.read_text()) if STATE.exists() else {}
    if "--restart-web" in sys.argv:
        pid = processes.get("assistant")
        if pid:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
            if str(ROOT / "server.py") in result.stdout:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                for _ in range(50):
                    if get(page_url + "/api/status") is None:
                        break
                    time.sleep(0.1)
        processes.pop("assistant", None)
        STATE.write_text(json.dumps(processes))
    if "--stop" in sys.argv:
        for name, pid in processes.items():
            # Only stop a recorded process if its command still belongs to this demo.
            result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
            command = result.stdout.strip()
            expected = str(ROOT / "server.py") if name == "assistant" else "ollama serve"
            if expected in command:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        STATE.write_text("{}")
        print("小伴的后台服务已停止。")
        return
    if settings["llm"]["manage_local_ollama"] and get("http://127.0.0.1:11434/api/tags") is None:
        env = os.environ.copy()
        env.update(OLLAMA_HOST="127.0.0.1:11434", OLLAMA_MODELS=str(ROOT / "models/ollama"))
        bundled = ROOT / "runtime/ollama/ollama"
        executable = str(bundled) if bundled.exists() else "/usr/local/bin/ollama"
        processes["ollama"] = start("ollama", [executable, "serve"], env)
        STATE.write_text(json.dumps(processes))
        for _ in range(40):
            if get("http://127.0.0.1:11434/api/tags") is not None:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Ollama 启动失败，请查看 .runtime/ollama.log")
    if get(page_url + "/api/status") is None:
        processes["assistant"] = start("assistant", [sys.executable, str(ROOT / "server.py")])
        STATE.write_text(json.dumps(processes))
        for _ in range(40):
            if get(page_url + "/api/status") is not None:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("页面服务启动失败，请查看 .runtime/assistant.log")
    print(f"小伴已启动：{page_url}")
    if "--no-open" not in sys.argv:
        webbrowser.open(page_url)


if __name__ == "__main__":
    main()
