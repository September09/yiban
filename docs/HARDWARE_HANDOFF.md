# 开发板接入与分工

这次整理保留 Mac 的现有体验，把模型、识别、朗读和动作执行从网页服务中拆开。
这是迁移基础，不代表已在 RK3588/RK182x 上验证。2026-11-07 已增加 Mac 浏览器端语音插话、记忆和声纹；USB 直采和实体动作尚未接入，详见 PERSONAL_FEATURES.md。

## 当前结构

```text
浏览器：界面、麦克风、摄像头、屏幕动画
    ↓ 原有 /api/* 接口
server.py：校验请求、组织对话、返回统一事件
    ├─ yiban/backends/models.py：Ollama / OpenAI 兼容模型服务
    ├─ yiban/backends/speech.py：SenseVoice 识别 / Mac 朗读 / 禁用语音
    └─ yiban/backends/__init__.py：组装服务 / 屏幕动作执行

yiban/audio/voice.py：现有唤醒和断句会话
yiban/actions.py：固定动作白名单与意图识别
yiban/settings.py + config/：选择运行后端
tools/diagnose.py：只读环境与模型连接检查
```

服务接口约定：

| 接口 | 输入与结果 | 后续接入位置 |
|---|---|---|
| models.chat(messages, vision=False) | 产生 message.content / done 事件；异常表示失败 | 已支持 Ollama NDJSON 与 OpenAI 兼容 SSE |
| models.status() | 返回 chat / vision 模型是否已列出 | 模型存在不保证能成功推理；需实际提问验证 |
| asr.transcribe(wav_bytes) | 单声道 PCM16 WAV → 文本 | 换成板端 ASR 或语音服务客户端 |
| tts.synthesize(text) | 返回音频字节和 MIME 类型 | Linux TTS 适配器；仍是整句返回 |
| actions.execute(action_id) | 返回动作事件 | 接真实控制器，验证执行结果后才能报告成功 |

输入采集和播放当前仍由浏览器负责。不能只改一个配置值就让板子脱离浏览器工作。
浏览器端已有 VAD 插话和请求取消；独立设备的全双工语音仍需要长连接音频管线、播放队列和回声处理。
OpenAI 兼容适配器仅接入对话文本/图片与流式回复，不执行模型工具调用。

## 保持 Mac 运行

原有双击启动、停止方式不变。没有 ASSISTANT_CONFIG 时使用 Mac 默认设置。

```bash
./.venv/bin/python -m tools.diagnose
./.venv/bin/python launcher.py --restart-web --no-open
```

配置更改需要重启服务。ASSISTANT_PORT 可覆盖监听端口。服务仍只监听本机。
当前名字和唤醒词没有改变；改名还需同步界面、角色设定、唤醒词文件及语音识别后的清理规则。

## 到货后先验证文字模型

1. 向厂商确认 RK1820 / RK1828 的准确型号、板卡型号、系统镜像、驱动与运行库版本。
2. 先运行厂商的模型样例，再启动模型 HTTP 服务。
3. 复制 config/board-text.example.json 为 config/board.local.json。
4. 将 base_url 改成实际地址（OpenAI 兼容地址包括 /v1），model 改成 /v1/models 返回的准确 ID。
   示例中的端口仅是占位，并不保证厂商默认使用 8080。vision_model 为空表示禁用视觉。
5. 如模型服务要求认证，设置 ASSISTANT_MODEL_API_KEY 环境变量；不要把密钥写进配置或提交仓库。

```bash
export ASSISTANT_CONFIG="$PWD/config/board.local.json"
python3 -m tools.diagnose
python3 server.py
```

文字配置不需要安装 numpy / sherpa-onnx，也不会自动启动 Ollama。建议 Python 3.10 或以上。
可先在 Mac 运行应用，把模型地址指向同一局域网的开发板；也可将应用运行在板上。
板上运行时，从 Mac 建立 SSH 转发，再打开 http://127.0.0.1:8765：

```bash
ssh -L 8765:127.0.0.1:8765 用户名@板卡IP
```

先停止 Mac 上占用 8765 的应用，或选用不同的本地转发端口并保持服务 Host 校验一致。
这个阶段只能文字对话和屏幕动作。自动朗读和语音按钮会因对应服务未启用而禁用。
浏览器摄像头和麦克风始终属于打开网页的那台机器，不会自动变成板上设备。
不把服务直接暴露到局域网；ESP32 接入时再设计独立的设备入口和认证。

## 后续任务与资源

| 阶段 | 我们处理 | 现场/厂商支持 | 通过条件 |
|---|---|---|---|
| 板端模型 | 接口配置、流式回复和错误排查 | 系统、加速卡驱动、匹配模型与运行库 | 文字提问能得到完整回复 |
| USB 音频 | 声道选择、格式转换、采集播放程序 | 数据线、扬声器、固件与回声参考路由 | 能同时录放，播放不被误认成用户说话 |
| 完整语音 | 连接 ASR、LLM、Linux TTS | 实际距离、噪声和音量测试 | 连续多轮可用，耗时和内存有记录 |
| 语音打断 | 已实现浏览器端取消生成、停止播放、保留插话；板端还需适配 | 声学问题时联系音频工程师 | 播放中插话能进入新一轮，不播放旧回复 |
| ESP32 无线 | 双向音频协议、重连与状态同步 | 烧录、串口、I2S 和供电验证 | 上下行连续稳定，断线能恢复 |
| 实体动作 | 白名单执行器、超时、停止接口 | 控制板协议、电机供电、机械限位 | 单个动作可验证，错误时不会报告成功 |

USB 语音阶段优先评估 OpenVoiceStream。它的具体接口、固件和模型版本以真机可用组合为准。
不直接把 Linux TTS 假定为某个 HTTP 协议；确认服务后再新增适配器。

参考：
- https://github.com/Seeed-Solution/openvoicestream
- https://github.com/airockchip/clawchips/blob/main/README_ZH.md
- https://wiki.seeedstudio.com/cn/respeaker_xvf3800_introduction/

## 验证边界

开发机：运行适配器测试、原有前端逻辑测试、Mac 模型/语音接口回归。
真实设备：板端推理、USB 拾音、回声消除、打断、ESP32 网络和电机动作必须另行测试。
诊断脚本只检查基础环境、文件和模型列表，不加载模型，也不代替声卡/NPU 验收。

2026-11-04 开发机验证结果：10 项后端/接口测试、前端逻辑测试通过；Mac 实际模型完成文字与合成图片问答；系统朗读生成有效 PCM16 WAV；合成语音完成唤醒、识别、动作指令与无声超时回归。Python 代码通过 3.10 语法检查。尚未验证真实浏览器录音、声学效果及开发板。
