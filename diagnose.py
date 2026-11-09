"""Read-only checks. Does not load inference models or acquire media devices."""
import json
import platform
import shutil
import sys
from pathlib import Path

from settings import ROOT, load_settings
from backends import build_services


def main():
    settings = load_settings()
    services = build_services(settings)
    result = {
        "system": platform.system(), "architecture": platform.machine(),
        "python": platform.python_version(),
        "free_disk_gib": round(shutil.disk_usage(ROOT).free / 1024**3, 1),
        "backends": {key: settings[key]["backend"] for key in ("llm", "asr", "tts", "actions")},
        "asr_files_present": services.asr.available(), "tts_available": services.tts.available(),
        "audio_input": settings["audio"]["input"],
        "barge_in_implemented": True, "hardware_actions_implemented": False,
        "speaker_model_present": Path(settings['speaker']['model']).is_file(),
        "memory_database_present": Path(settings['memory']['database']).is_file(),
        "note": "不加载模型、不录音、不拍照。文件存在不代表已验证推理；声卡、NPU 驱动和声学效果需真机验证。",
    }
    try:
        result["models"] = services.models.status()
    except Exception as error:
        result["models"] = {"chat": False, "vision": False}
        result["model_error"] = type(error).__name__  # Do not print URLs, credentials or server error bodies.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["models"]["chat"] else 1


if __name__ == "__main__":
    sys.exit(main())
