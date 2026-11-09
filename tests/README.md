# 测试目录

所有命令在项目根目录运行。测试代码随项目上传；生成的测试音频／图像放在被忽略的 `.runtime/`，不用上传。日常启动助手不会自动运行测试。

## 一次运行全部常规检查

需要项目 Python 环境及 Node.js：

```bash
# Mac / Linux
./.venv/bin/python tests/run_all.py
```

```powershell
# Windows（入口兼容，尚未完成真机验收）
.\.venv\Scripts\python.exe tests/run_all.py
```

入口依次运行后端单元／HTTP 测试和前端逻辑检查，失败时返回非零退出码。后端使用临时数据库和模拟服务；本地音频模型或合成素材缺失时会跳过部分用例，不会自动下载模型或安装依赖。

## 按功能找文件

| 文件 | 主要检查内容 |
|---|---|
| `test_memory_*.py` | 个人记忆、导入去重、编辑历史与冲突确认 |
| `test_shared_*.py`、`test_material_import.py` | 共同记忆权限、原文依据、资料导入与语音确认 |
| `test_personal*.py`、`test_speaker_diagnostics.py` | 档案、称呼、声纹与音频诊断 |
| `test_backends.py`、`test_cloud_setup.py` | 模型接口、取消、配置与云端启动 |
| `test_conversation_diagnostics.py`、`test_speech_preferences.py` | 对话诊断与音色设置 |
| `check_frontend.cjs` | 网页交互、隔离、取消和试听逻辑（模拟浏览器） |
| `make_audio_fixtures.py`、`check_pipeline.py` | 合成测试素材及实际运行管线，需单独执行 |

实际管线会调用当前模型与语音服务，不在常规入口中自动运行。使用方法及真机验证边界见 [项目说明](../README.md#9-排查与验证)。
