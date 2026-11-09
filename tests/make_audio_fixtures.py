"""Create reproducible synthetic test audio on macOS; never opens a microphone."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backends.speech import MacSpeech
from settings import load_settings

TEXTS = {
    'xiaoban-wake': '你好小伴',
    'xiaoban-command': '请点点头',
    'xiaoban-negative': '今天下午天气不错，我们可以出去走一走。',
    'speaker-1': '今天我想和机器人聊聊天，也希望它能慢慢记住我的习惯。',
    'speaker-2': '周末我喜欢做一些自己感兴趣的事情，生活里有很多值得发现的细节。',
    'speaker-3': '这是我的第三段声音，我会用自然的语速说话，让它认识我的声音。',
    'speaker-heldout': '现在请辨认我的声音，这段话没有出现在之前的录入内容里。',
}

if __name__ == '__main__':
    output = ROOT / '.runtime/test-audio'
    output.mkdir(parents=True, exist_ok=True)
    tts = MacSpeech(load_settings()['tts'])
    with tempfile.TemporaryDirectory() as folder:
        original = Path(folder)/'original.wav'
        for name, text in TEXTS.items():
            target = output/(name+'.wav')
            if target.exists():
                continue
            original.write_bytes(tts.synthesize(text)[0])
            subprocess.run(['/usr/bin/afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(original), str(target)], check=True)
    print('Synthetic test fixtures ready:', output)
