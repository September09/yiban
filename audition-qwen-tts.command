#!/bin/zsh
set -eu
cd "${0:A:h}"
PYTHON=".runtime/qwen3-tts/.venv/bin/python"
SCRIPT=".runtime/qwen3-tts/audition-20261116/generate.py"
if [[ ! -x "$PYTHON" || ! -f "$SCRIPT" ]]; then
  print '未找到本机 Qwen3-TTS 试听环境或脚本。'
  read -r '?按回车退出…'
  exit 1
fi
print '正在离线生成三种音色的样音，首次生成请耐心等待。'
if ! "$PYTHON" "$SCRIPT"; then
  print '生成失败，请把上面的错误信息发给我。'
  read -r '?按回车退出…'
  exit 1
fi
for voice in serena vivian uncle_fu; do
  print "现在播放：$voice"
  /usr/bin/afplay ".runtime/qwen3-tts/audition-20261116/$voice.wav"
done
print '试听结束。主页面声音设置未更改。'
read -r '?按回车退出…'
