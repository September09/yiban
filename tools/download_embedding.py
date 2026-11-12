"""Install the official small Qwen embedding model using verified Ollama registry blobs."""
import json
import shutil
from tools import download_vision as registry


def main():
    registry.CACHE = registry.ROOT / 'models/embedding-download'
    registry.REGISTRY = 'https://registry.ollama.ai/v2/library/qwen3-embedding'
    registry.CACHE.mkdir(parents=True,exist_ok=True)
    registry.BLOBS.mkdir(parents=True,exist_ok=True)
    manifest_path=registry.CACHE/'manifest.json'
    registry.curl(registry.REGISTRY+'/manifests/0.6b',manifest_path)
    manifest=json.loads(manifest_path.read_text())
    layers=[manifest['config'],*manifest['layers']]
    if sum(x['size'] for x in layers)>2_000_000_000:raise ValueError('Unexpected model size')
    for layer in layers:registry.download(layer)
    dest=registry.ROOT/'models/ollama/manifests/registry.ollama.ai/library/qwen3-embedding/0.6b'
    dest.parent.mkdir(parents=True,exist_ok=True)
    temporary=dest.with_suffix('.installing');shutil.copyfile(manifest_path,temporary);temporary.replace(dest)
    print('Qwen3-Embedding 0.6B installed; every blob SHA-256 verified.',flush=True)


if __name__=='__main__':main()
