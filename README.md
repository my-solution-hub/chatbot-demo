# Nova Sonic 语音演示

基于 Amazon Nova Sonic (`amazon.nova-2-sonic-v1:0`) 的语音对话演示，运行在 Amazon Bedrock 上。对它说话，它会语音回复，并在需要时调用两个简单工具（`get_current_time`、`get_weather`）。这是一个可以在 30 分钟内读完并在此基础上扩展的起点，而非生产级参考实现。

## 效果展示

```
Nova Sonic Demo: model=amazon.nova-2-sonic-v1:0 region=ap-northeast-1
LISTENING: ready for speech
USER: what's the weather in seattle?
TOOL_CALL: get_weather {"city":"Seattle"}
TOOL_RESULT: get_weather {"city":"Seattle","condition":"rainy","temperature_c":14}
ASSISTANT: It's rainy in Seattle, about 14 degrees Celsius.
```

你说话，模型通过扬声器回复，工具调用实时显示在标准输出。

## 1. 前置条件

- **Python 3.12**（`aws-sdk-bedrock-runtime` 要求）

  推荐使用 [pyenv](https://github.com/pyenv/pyenv) 管理 Python 版本：

  ```bash
  # 安装 pyenv (macOS)
  brew install pyenv

  # 安装 pyenv (Linux)
  curl https://pyenv.run | bash
  # 然后将 pyenv 添加到 shell — 参见 https://github.com/pyenv/pyenv#set-up-your-shell-environment

  # 安装 Python 3.12 并设置为本项目使用
  pyenv install 3.12
  pyenv local 3.12          # 在项目根目录创建 .python-version
  python --version          # 应输出 Python 3.12.x
  ```

  Windows 用户请使用 [Python.org 安装包](https://www.python.org/downloads/) 3.12.x。

- **PortAudio**（CLI 模式下 `sounddevice` 使用的麦克风和扬声器绑定）
  | 平台 | 安装命令 |
  | --- | --- |
  | macOS | `brew install portaudio` |
  | Debian / Ubuntu | `sudo apt-get install libportaudio2` |
  | Windows | `sounddevice` wheel 已内置，无需额外安装 |

- **AWS 账户**，已开通 Amazon Bedrock 访问权限并启用 **Nova Sonic** 模型。当前支持的区域：
  - `us-east-1`（弗吉尼亚北部）
  - `us-east-2`（俄亥俄）
  - `us-west-2`（俄勒冈）
  - `ap-northeast-1`（东京）

- **AWS 凭证**，可通过标准 SDK 链解析（环境变量、`~/.aws/credentials`、命名配置文件、SSO、IAM 角色等 boto3 支持的任何方式）

> **亚太用户提示：** 东京（`ap-northeast-1`）延迟最低、音频质量最好。如果你的账户在东京没有 Sonic 访问权限，俄勒冈（`us-west-2`）是次优选择。

## 2. 安装

```bash
git clone <this repo>
cd chatbot-demo

# 确保使用 Python 3.12（pyenv 用户：pyenv local 3.12）
python --version   # 必须是 3.12.x

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -r requirements.txt     # 运行时依赖
# （可选）pip install -r requirements-dev.txt  # 添加 pytest + hypothesis
```

## 3. 配置 AWS 访问

演示使用标准 AWS SDK 凭证链，选择你已有的方式：

```bash
# 方式 A：环境变量
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=ap-northeast-1

# 方式 B：命名配置文件（使用 ~/.aws/credentials）
export AWS_PROFILE=my-profile
export AWS_REGION=ap-northeast-1

# 方式 C：SSO
aws sso login --profile my-profile
export AWS_PROFILE=my-profile
export AWS_REGION=ap-northeast-1
```

IAM 主体需要对所选区域的 Nova Sonic 模型拥有 `bedrock:InvokeModelWithBidirectionalStream` 权限。如果凭证或区域缺失或错误，演示会打印明确的错误信息并以下表中的退出码退出。

## 4. 运行

### CLI 模式（需要本地麦克风和扬声器）

```bash
python -m nova_sonic_demo
```

对着麦克风说话。按 **Ctrl+C** 停止 — 演示会排空音频、关闭 Bedrock 会话，并在 5 秒内退出。

### Web UI 模式（浏览器，无需本地音频驱动）

```bash
python -m nova_sonic_demo.web
# 打开 http://127.0.0.1:8000
```

在浏览器中点击 **Start** 按钮开始语音会话，点击 **Stop** 结束。支持实时转录显示和工具调用日志。

### 试试这些提示

- "Hello." — 简单对话，不调用工具
- "What time is it in Tokyo?" — 调用 `get_current_time`
- "What's the weather in Seattle?" — 调用 `get_weather`
- "Tell me the weather in Paris and the time in New York." — 一轮对话中调用两个工具

## 5. 音频调优（跨区域使用时推荐）

Nova Sonic 双向传输未压缩的 16-bit PCM，长距离链路可能会出现卡顿。默认配置已包含三项优化：

- **语音活动检测（VAD）**：静音帧被丢弃，80 ms 的帧被合并为一个事件
- **半双工回声门控**：扬声器播放助手语音时麦克风静音，防止模型自言自语
- **播放器抖动缓冲**：播放前缓冲 250 ms 音频，吸收跨区域网络抖动

如果仍有卡顿，尝试以下方式：

```bash
# 音频卡顿或延迟：增大抖动缓冲
python -m nova_sonic_demo --prebuffer-ms 400

# 麦克风持续拾取风扇/电视声：更严格的 VAD
python -m nova_sonic_demo --vad-aggressiveness 3

# 戴耳机（无回声路径）：释放双向通道以支持打断
python -m nova_sonic_demo --no-echo-cancel

# 带宽受限：更大的批次，更长的挂起时间
python -m nova_sonic_demo --vad-batch-frames 6 --vad-hangover-ms 1200

# 与未调优的基线对比
python -m nova_sonic_demo --no-vad --no-echo-cancel --prebuffer-ms 0
```

所有参数：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--no-vad` | 关闭 | 流式传输每一帧麦克风数据，不做语音门控 |
| `--no-echo-cancel` | 关闭 | 禁用扬声器播放时的麦克风静音。仅在使用耳机时推荐 |
| `--vad-aggressiveness` | `2` | webrtcvad 严格度，0（宽松）到 3（严格） |
| `--vad-frame-ms` | `20` | VAD 窗口：10、20 或 30 ms |
| `--vad-batch-frames` | `4` | 合并为一个 Bedrock 事件的 VAD 帧数。4 = 80 ms。越大开销越低 |
| `--vad-hangover-ms` | `800` | 最后一个语音帧后继续流式传输的时长，避免截断 |
| `--vad-preroll-ms` | `200` | 门控打开时包含的预触发音频量（保留第一个音素） |
| `--prebuffer-ms` | `250` | 播放器抖动缓冲预热。越大隐藏越多抖动，但增加延迟 |

## 6. 标准输出格式

每个前缀代表对话中的一个事件：

| 前缀 | 含义 |
| --- | --- |
| `Nova Sonic Demo: model=... region=...` | 启动横幅 |
| `LISTENING: ready for speech` | 麦克风就绪，可以说话 |
| `USER: <text>` | 模型听到你说的话（最终转录） |
| `TOOL_CALL: <name> <args-json>` | 模型决定调用工具 |
| `TOOL_RESULT: <name> <result-json>` | 工具返回结果给模型 |
| `ASSISTANT: <text>` | 模型的回复（最终转录；音频同时播放） |

每轮对话只打印一次。如果某行缺失，说明该步骤未发生。

## 7. 故障排除

| 症状 | 可能原因 | 解决方法 |
| --- | --- | --- |
| `Missing input device`（退出码 3） | 未检测到麦克风 | 插入/选择默认麦克风 |
| `Region <r> does not support Nova Sonic v2`（退出码 2） | 你的 `AWS_REGION` 不支持 Bedrock Sonic | 使用上述支持的区域 |
| `AWS credentials missing or invalid`（退出码 4） | 凭证链为空 | 设置 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` 或 `AWS_PROFILE` |
| `Bedrock open failed (auth): ...`（退出码 5） | 凭证本地有效但 Bedrock 拒绝 | 验证 IAM 主体对 Nova Sonic 有 Bedrock 模型调用权限 |
| 演示运行但从未出现 `USER:` 行 | 麦克风音量太低或系统麦克风静音 | 检查系统声音设置；尝试 `--no-vad` 验证采集 |
| 助手自言自语循环 | 回声门控禁用且未使用耳机 | 去掉 `--no-echo-cancel` 或戴上耳机 |
| 音频卡顿/机器人声 | 跨区域抖动 | 增大 `--prebuffer-ms`（试试 `400`）或使用更近的区域 |

## 8. 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常关闭（Ctrl+C） |
| `2` | 不支持的 AWS 区域 |
| `3` | 缺少麦克风或扬声器 |
| `4` | AWS 凭证缺失或无效 |
| `5` | Bedrock 会话无法打开（认证/网络/区域/模型） |

## 9. 架构概览

运行时是一个 asyncio 事件循环。核心模块：

| 模块 | 职责 |
| --- | --- |
| `nova_sonic_demo/cli.py` | 生命周期：启动、事件路由、Ctrl+C 关闭 |
| `nova_sonic_demo/session.py` | Bedrock 双向流封装；打开会话、发送/接收事件 |
| `nova_sonic_demo/audio.py` | 麦克风采集、VAD 门控批处理、扬声器播放（含抖动缓冲） |
| `nova_sonic_demo/events.py` | Nova Sonic 输入/输出事件的构建器和解析器 |
| `nova_sonic_demo/tools/` | 工具注册表、调度器（含超时和 Schema 验证）、两个演示工具 |
| `nova_sonic_demo/logging.py` | stdout 前缀日志器 |
| `nova_sonic_demo/web/` | Web UI：FastAPI 服务器 + WebSocket + 浏览器客户端 |

## 10. 添加自定义工具

只需修改 `nova_sonic_demo/tools/registry.py`：

```python
async def my_tool(args: dict) -> dict:
    # 验证参数、执行操作、返回 JSON 可序列化的 dict
    return {"status": "ok"}

# 与现有工具一起注册：
ToolDefinition(
    name="my_tool",
    description="Does the thing.",
    schema={
        "type": "object",
        "properties": {"...": {"type": "string"}},
        "required": ["..."],
    },
    handler=my_tool,
)
```

工具调用在同一个 asyncio 循环中运行，每次调用 10 秒超时，带 JSON Schema 验证。错误会返回给模型，让它优雅地道歉而不是崩溃。

## 11. 运行测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

测试套件包含基于 `hypothesis` 的属性测试，覆盖工具调度、确定性模拟天气、时区解析、日志语法、会话生命周期和调度器延迟边界。

## 12. 本演示刻意未实现的功能

这是一个起点。以下功能刻意**未实现**以保持代码可读性：

- 唤醒词检测（"Hey Nova"）
- 超过 8 分钟 Bedrock 连接限制的多会话续接
- 真实天气 API 集成（内置的 `get_weather` 返回确定性模拟数据，演示可离线运行）
- 电话/SIP 集成
- 真正的声学回声消除（演示使用更简单的半双工静音；在扬声器上效果良好，但不支持打断）

理解核心循环后，这些都很容易在此基础上添加。

## 许可证

参见 [`LICENSE`](LICENSE)。
