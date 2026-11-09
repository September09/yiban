#!/bin/zsh
cd -- "${0:A:h}"
./.venv/bin/python launcher.py
if [ $? -ne 0 ]; then
  echo "启动未完成，请查看上方错误。按回车关闭。"
  read
fi
