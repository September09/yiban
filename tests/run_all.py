"""Run backend and frontend checks from any working directory."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    node = shutil.which("node")
    if not node:
        print("缺少 Node.js，无法检查网页逻辑；请先安装 Node.js，再运行本命令。", file=sys.stderr)
        return 2
    checks = [
        ("后端测试", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]),
        ("前端逻辑测试", [node, "tests/check_frontend.cjs"]),
    ]
    failed = []
    for name, command in checks:
        print(f"\n正在运行：{name}", flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            failed.append(name)
    if failed:
        print("\n未通过：" + "、".join(failed), file=sys.stderr)
        return 1
    print("\n后端与前端检查均通过。音频模型或素材缺失时，部分用例可能跳过，请查看上方结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
