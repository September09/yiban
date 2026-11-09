"""Fetch the official Ollama model through curl; verify every SHA-256 before install.

The host's proxy returns synthetic DNS addresses rejected by Ollama's downloader.
This downloader keeps TLS verification and uses only the official model registry.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "models/vision-download"
BLOBS = ROOT / "models/ollama/blobs"
REGISTRY = "https://registry.ollama.ai/v2/library/qwen2.5vl"


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def curl(url, output, extra=()):
    subprocess.run(["curl", "-sSL", "--fail", "--retry", "5", "--retry-all-errors",
                    "--connect-timeout", "25", "--speed-time", "60", "--speed-limit", "1024",
                    *extra, "-o", str(output), url], check=True)


def download(layer):
    digest = layer["digest"].removeprefix("sha256:")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid manifest digest")
    target = BLOBS / ("sha256-" + digest)
    if target.exists() and target.stat().st_size == layer["size"] and checksum(target) == digest:
        return
    url = REGISTRY + "/blobs/sha256:" + digest
    assembled = CACHE / (digest + ".assembled")
    if layer["size"] > 100_000_000:
        size = layer["size"]
        part_size = (size + 15) // 16
        def part(index):
            start = index * part_size
            end = min(size, start + part_size) - 1
            output = CACHE / f"{digest}.{index:02d}.part"
            expected = end - start + 1
            if not output.exists() or output.stat().st_size != expected:
                curl(url, output, ("--range", f"{start}-{end}"))
            if output.stat().st_size != expected:
                raise ValueError(f"Part {index} length mismatch")
            print(f"模型分片 {index + 1}/16 完成", flush=True)
            return output
        with ThreadPoolExecutor(max_workers=8) as executor:
            parts = list(executor.map(part, range(16)))
        with assembled.open("wb") as dest:
            for path in parts:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, dest, 1024 * 1024)
    else:
        curl(url, assembled)
    if assembled.stat().st_size != layer["size"] or checksum(assembled) != digest:
        raise ValueError("模型校验失败，不安装。")
    assembled.replace(target)
    print(f"文件 SHA-256 验证通过：{digest[:12]}", flush=True)


if __name__ == "__main__":
    CACHE.mkdir(parents=True, exist_ok=True)
    BLOBS.mkdir(parents=True, exist_ok=True)
    manifest_file = CACHE / "manifest.json"
    curl(REGISTRY + "/manifests/3b", manifest_file)
    manifest = json.loads(manifest_file.read_text())
    for layer in [manifest["config"], *manifest["layers"]]:
        download(layer)
    dest = ROOT / "models/ollama/manifests/registry.ollama.ai/library/qwen2.5vl/3b"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest_file, dest)
    print("视觉模型已安装，全部文件校验通过。", flush=True)
