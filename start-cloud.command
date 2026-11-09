#!/bin/zsh
cd -- "${0:A:h}"
./.venv/bin/python -m tools.cloud_setup
if [ $? -ne 0 ]; then
  echo "云端启动未完成，请查看上方提示。按回车关闭。"
  read
fi
