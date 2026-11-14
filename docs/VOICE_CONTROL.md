# GG Bot 语音控制（本地源码接入）

此功能以 `voice_control` 插件提供，默认关闭。它不改变原来的文字点歌指令。

流程：频道成员喊 **GG Bot** → 频道里响短提示音 → 只收取唤醒者接下来最多 10 秒的语音 → 本地 Fun-ASR-Nano 识别 → 复用现有音乐操作。

## 组成

- 机器人：内置 Vosk 英文小模型用于唤醒；Agora Web SDK 订阅远端成员的独立音轨，并转换为 16 kHz 单声道 PCM。
- 本地识别服务：`tools/voice_asr_server.py`，支持已准备好的 Apple Silicon MLX Fun-ASR-Nano 模型。与机器人进程/容器独立运行，通过带密钥的 HTTP 接口交互。
- 命令：`src/voice_control/intent.py`，包含点歌、B站、下一首、暂停/继续、停止、音量、静音、播放模式、当前歌曲/队列查询、清空队列、帮助、取消等 22 类说法。
- 执行：`src/voice_control/actions.py`，调用原有音乐方法；不靠识别文字拼接并执行系统命令。

Vosk 唤醒模型和依赖随镜像内置；Nano 模型和 MLX 不加入默认镜像；未启用时不加载 Vosk、不接收频道音频，也不发起识别请求。

## 准备

源码部署安装项目依赖即可；镜像已包含 Vosk。兼容的单独依赖安装方式：

```sh
python -m pip install -r requirements-voice.txt
```

Vosk 英文小模型随插件放在 `plugins/voice_control/models/vosk-model-small-en-us-0.15/`，模型路径留空即可自动使用。也可以填写其他模型目录；该目录应包含 `am/final.mdl`，不是 ZIP 文件。

Mac 宿主机使用单独的 Python 3.12 环境安装：

```sh
python3.12 -m venv .venv-voice-asr
.venv-voice-asr/bin/python -m pip install -r requirements-voice-asr.txt
```

准备兼容 `mlx-audio==0.6.5` 的本地 `fun_asr_nano` 模型导出，可复用独立语音实验里已验证的 `models/fun-asr-nano-8bit/`。模型下载和量化步骤见 MLX Audio 的 Fun-ASR-Nano 文档，不使用旧的 `model_type=funasr` 导出。

设置自己的随机访问密钥，再启动宿主机识别服务：

```sh
export VOICE_ASR_TOKEN='替换为你自己的随机访问密钥'
.venv-voice-asr/bin/python tools/voice_asr_server.py \
  --model /绝对路径/fun-asr-nano-8bit --host 127.0.0.1 --port 18767
```

运行时只读取本地模型，禁止自动联网下载。默认只监听本机；如需从容器访问，请选择容器实际可达的宿主机地址和监听方式。这个密钥只用于本地识别服务，不是 Oopz 或网易云凭据。

## 后台配置

在后台“插件”里编辑 `voice_control`：

| 字段 | 含义 |
| --- | --- |
| `enabled` | 总开关，默认 `false` |
| `wake_volume` | 独立提示音音量，0～200，默认 150；不影响音乐音量 |
| `area` | 待命所在域 ID；文字点歌后可跨域跟随 |
| `voice_channel` | 待命语音频道 ID；跟随机器人当前所在域与频道，停止播放后返回待命频道 |
| `text_channel` | 初始消息频道；跟随点歌后使用对应域最近一次文字点歌的频道 |
| `wake_model_path` | 留空使用插件内置 Vosk 模型；指定其他模型时相对路径以项目根目录为准 |
| `asr_url` | 本地识别服务地址，不带路径；原生运行例 `http://127.0.0.1:18767`，Docker Desktop 可配置 `http://host.docker.internal:18767` |
| `asr_token` | 与 `VOICE_ASR_TOKEN` 一致 |
| `allowed_uids` | 留空允许待命频道内所有说话者，不查询或校验账号身份；填写后仅允许列表中的 UID |
| `hotwords` | 歌手/歌名热词，每行一个，最多 40 个，合计不超过 1000 字符 |

配置保存会重载插件。启用时需要模型和依赖已经就绪；配置不完整会明确拒绝加载。

启用后，机器人空闲时进入待命频道。通过文字点歌进入其他域或语音频道后，唤醒会跟随机器人当前频道。切换频道会清空上一频道的语音片段和选歌确认。在待命频道停止播放只停止音乐并保留连接；在其他频道停止播放或退出语音后，机器人自动返回待命频道；待命请求会在连接锁内再次检查空闲状态，避免抢走文字点歌的新连接。停用语音插件可结束待命。

## 使用

提示音后说：

- `播放晴天`、`播放周杰伦的晴天`、`播放 Taylor Swift 的 Love Story`。
- `B站播放某个关键词`、`随机来一首`。
- `下一首`、`暂停`、`继续播放`、`停止播放`。
- `声音大一点`、`声音小一点`、`音量调到五十`、`静音`、`取消静音`。
- `单曲循环`、`顺序播放`、`随机播放队列`、`这首播完就停`。
- `现在什么歌`、`查看队列`、`清空队列`、`帮助`、`取消`。

“随机来一首”从喜欢列表选歌；“随机播放队列”只改变队列播放顺序。含糊的“随机播放”、否定句和多条混说会要求重说。

搜索结果多条或不能确定匹配时，先发出候选；同一位用户再次唤醒后说“第一首/第二首/第三首”或“确认”，45 秒内有效。其他人不能确认你的候选，播放会话改变后旧确认失效。

## 边界与验证

- 接收上限 8 路成员音轨，音频帧队列上限 120 包。只有唤醒者的指令片段才送往配置的本地识别服务；不保存录音，不把频道全部聊天上传做持续转写。
- 默认按远端语音流区分说话者，不要求账号身份匹配；配置允许 UID 列表时才查询成员身份。跨频道或识别期间播放会话改变时，丢弃指令。
- 提示音是额外音轨，不替换音乐音轨，不修改持久化音量。
- 机器人侧 Vosk 依赖已加入默认构建。只上传插件 ZIP 无法补齐这次底层语音接收扩展；后续部署需使用包含这些源码的机器人版本。
- 自动验证包含身份/频道隔离、慢识别结果丢弃、候选确认、播放控制调用、可选关闭、识别服务鉴权与 PCM 限制，以及接收脚本的采样率转换、缓冲上限、提示音不替换音乐和卸载清理。
- 接收脚本测试可运行 `node tests/voice_receiver.cjs`；Python 验证可运行 `python -m unittest discover -s tests -p "test_voice*.py"`。
- 实际 Oopz 频道收音、提示音可闻性、权限和多人环境需要部署后真实联调；模拟测试不能替代这个验收。

本次 SDK 最小扩展位于 `src/oopz_sdk/assets/voice/agora_player.html`、新增 `voice_control.js` 及 `transport/voice_browser.py`；项目的 Playwright 与 Selenium 后端共用同一页面和接口。

参考：Vosk 官方模型 <https://alphacephei.com/vosk/models>；MLX Audio Fun-ASR-Nano 文档 <https://github.com/Neywa-Labs/mlx-audio/blob/main/docs/models/stt/fun-asr-nano.md>；Agora Web SDK 远端音频回调 <https://api-ref.agora.io/en/video-sdk/web/4.x/interfaces/iremoteaudiotrack.html>。
