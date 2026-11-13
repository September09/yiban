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

- `test_read_only_agent.py`：只读动作白名单、参数校验、动态动作约束、查询/读取/时钟、摘录与权限隔离、引用映射、活动澄清、取消/超时和 HTTP 路由。使用临时库与可控模型回复。

- `test_memory_agent.py`：新增/修改预览不写入、人工确认、冻结内容、取消/过期/重复请求、具体会话与空间绑定、权限、冲突、版本、候选选择、默认范围、明确成员日期及HTTP入口。

- `test_agent_audit.py`：本人记录与权限隔离、预览/选择/确认关联、内容排除、保留期、重启恢复、日志失败不影响保存、实际用量统计、实时步骤和HTTP认证。前端另验证刷新、筛选、展开状态及切换后的迟到结果清理。

- `test_agent_evaluation.py`、`test_agent_quality_guards.py`：评测器边界、无依据引用、新旧版本范围、比较读取完整性、同名日期澄清、结构化对照和权限。真实本机模型评测单独执行 `python -m tools.evaluate_agent --output .runtime/evaluation/新目录`，见 [文字评测说明](../docs/AGENT_TEXT_EVALUATION.md)。

- `test_shared_replacement.py`：替代预览不写入、独立确认、会话与版本、权限、冻结参数、事务回滚、原文与作者保留、替代链隔离、局部修改及最初安排引用；全部使用临时数据。前端检查覆盖替代确认、手动入口、清理与程序定位引用。

- `test_answer_feedback.py`：反馈入口绑定、明确同意、重试去重、本人隔离、权限及版本变化后的快照遮蔽、处理进度与 HTTP 路由。前端另检查同意门槛、迟到结果清理和复测问题不自动发送。

`test_shared_backup.py`：共同记忆整空间备份、跨库账号对应、角色与可见范围、原文和历史、替代关联、校验及数量限制、确认会话和过期、取消、回滚和幂等恢复。含真实HTTP路由；仅使用临时数据库。前端检查另覆盖文件读取、预览双勾选、编辑使预览失效以及退出后的异步结果隔离。

`test_shared_recovery.py`：16项恢复与HTTP测试，涵盖回收站保留和查询隔离、原文／历史、当前角色及成员、历史恢复不扩大范围、末端替代纠正、确认令牌会话／版本／期限、原子回滚与重试、幂等和重启后确认、容量及冲突。前端另验双重确认、迟到结果、取消及刷新锁释放。全部使用隔离数据。

`test_personal_retrieval.py`：11项个人检索和真实HTTP测试，验证同义问法、档案与访客隔离、只读、引用、回退、缓存版本和忘记后清理、资料变化拒绝、取消及聊天候选。前端检查另验引用／候选分组、禁止自动朗读、切换清空和迟到响应。

`test_shared_batches.py`：14项批次及HTTP测试，验证命名导入、原文精确追溯、未归档不保存原文、权限与数量隔离、修改和替代保留、双重确认、会话／版本／期限、整批容量、失败回滚、持久重试、回收站恢复、检索失效及新旧备份兼容。前端另覆盖确认门槛、改原因失效、退出后的迟到原文清理和不持久保存。

`test_diagnostic_overview.py`：13项统一总览及HTTP用例，验证候选／读取／引用分组、未知与真实零值、模型信息不含地址或密钥、原文不落诊断库、权限／版本变化遮蔽、本人和空间隔离、处理中信息、HTTP错误／超时／取消、旧记录兼容及规则与记忆指令区分。前端另测范围筛选、模型与耗时、失败原因、普通聊天不误标引用、规则0引用、退出和迟到响应清理。

- `test_model_settings.py`：模型档位保存／固定整轮配置、连接检测、受授权的对比与故障隐私边界（临时数据库、模拟服务）。
- `node tests/check_model_settings.cjs`：对比预览确认、取消、迟到结果清理，不访问媒体或持久浏览器存储。

- `test_audio_devices.py`：只读系统音频元数据、字段过滤／缓存／错误、回环HTTP限制；不调用录音或合成。
- `node tests/check_audio_devices.cjs`：模拟档位与精确路由、断开／权限／存储／迟到配置，真实录音类配模拟轨道，无真实收音、播放和音量更改。

- `test_personal_state.py`：个人状态／日期、有限冲突规则、成对确认及回滚、迁移、状态过滤与版本、导入导出、HTTP隔离（虚构临时库）。
- `node tests/check_personal_state.cjs`：文字预览／确认、草稿失效和档案切换的迟到结果保护，无录音或播放。

- `test_appearance.py`：IP配置、留空／损坏回退、PNG限制、原子保存／版本冲突、HTTP同源与活动语音保护、真实KWS词表兼容；不采集或播放声音。
- `node tests/check_branding.cjs`：预览与保存分离、名称与主题同步、缺少／失败形象回退、录音期间禁用和迟到状态保护；不访问媒体、不改变音量。
