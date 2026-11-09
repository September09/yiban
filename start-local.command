#!/bin/zsh
cd -- "${0:A:h}"
export ASSISTANT_CONFIG="$PWD/config/mac.json"
./.venv/bin/python launcher.py --restart-web
if [ $? -ne 0 ]; then
  echo "本地启动未完成，请查看上方提示。按回车关闭。"
  read
fi
