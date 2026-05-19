# 测试文档

## 概览

项目共有 **326 个自动化测试**，分布在 17 个测试文件中，覆盖核心模块、Web UI、云部署组件和属性测试。

运行方式：
```bash
pip install -r requirements-dev.txt
pytest -q
```

## 测试分布

| 测试文件 | 测试数 | 覆盖模块 |
|---------|--------|---------|
| `test_events.py` | 52 | Nova Sonic 事件构建器和解析器 |
| `test_web_messages.py` | 35 | WebSocket 消息类型和验证 |
| `test_agentcore_session_manager.py` | 25 | AgentCore 会话管理器（云模式） |
| `test_logger.py` | 25 | 控制台日志器（前缀语法、会话门控） |
| `test_deployment_config.py` | 24 | 部署配置（local/cloud 模式切换） |
| `test_audio.py` | 20 | 音频采集、播放、VAD 门控 |
| `test_tool_handlers.py` | 18 | 本地工具处理器（时间、天气） |
| `test_lambda_handlers.py` | 18 | Lambda 工具处理器（AgentCore 格式） |
| `test_config.py` | 18 | AWS 区域/凭证解析、异常类型 |
| `test_web_logger.py` | 15 | WebLogger（WebSocket 路由） |
| `test_tool_dispatcher.py` | 15 | 工具调度器（超时、验证、日志） |
| `test_session_manager.py` | 15 | 本地 SessionManager（状态机、音频转发） |
| `test_tool_registry.py` | 12 | 工具注册表（Schema、Bedrock 配置） |
| `test_session.py` | 11 | SonicSession（Bedrock 双向流） |
| `test_cli.py` | 11 | CLI 生命周期（启动、关闭、退出码） |
| `test_properties.py` | 8 | 属性测试（hypothesis） |
| `test_session_factory.py` | 4 | 会话管理器工厂（模式选择） |

## 按功能域分类

### 1. 核心语音会话（106 个测试）

**SonicSession (`test_session.py` — 11 tests)**
- 打开握手顺序（sessionStart → promptStart → contentStart）
- 超时处理（BedrockOpenError with timeout category）
- 音频发送（base64 编码验证）
- 工具调用往返（toolUse → dispatch → toolResult triple）
- 关闭幂等性和终止符顺序
- 并发写入序列化（write lock 验证）
- System prompt 文本三元组

**事件构建器/解析器 (`test_events.py` — 52 tests)**
- 所有输入事件构建器的 JSON 可序列化性
- 输出事件解析：音频（base64 解码）、文本（角色验证）、工具调用
- 边界情况：畸形 payload、缺失字段、未知事件类型

**音频 (`test_audio.py` — 20 tests)**
- AudioCapturer：流参数、pump 回调、停止幂等性
- AudioPlayer：PCM 排空、静音填充、容量溢出、预缓冲预热、is_playing 状态
- VADGate：静音丢弃、语音流式传输（含 preroll）、hangover 关闭、回声门控、flush

**CLI 生命周期 (`test_cli.py` — 11 tests)**
- 正常路径（返回 0，打印 banner + LISTENING）
- 错误退出码：设备缺失(3)、区域不支持(2)、凭证缺失(4)、Bedrock 打开失败(5)
- 音频事件路由到 player
- 关闭顺序（capturer → player → session）
- KeyboardInterrupt 映射到退出码 0

### 2. 配置和工具（63 个测试）

**配置 (`test_config.py` — 18 tests)**
- 区域解析优先级（AWS_REGION > AWS_DEFAULT_REGION > boto3 session）
- 区域验证（支持的 4 个区域 + 拒绝不支持的）
- 凭证断言（存在/缺失/异常）
- 异常类型（BedrockOpenError、MissingDeviceError）

**工具注册表 (`test_tool_registry.py` — 12 tests)**
- 默认注册表包含 2 个工具
- 描述长度验证（1-200 字符）
- Schema 结构验证
- to_bedrock_config() 输出格式

**工具调度器 (`test_tool_dispatcher.py` — 15 tests)**
- 成功调度（通过默认注册表）
- 未知工具返回 error
- Schema 验证失败返回 invalid_arguments
- 超时返回 tool_timeout
- 异常消息截断到 200 字符
- 日志在 handler 执行前记录
- 无状态性验证

**本地工具处理器 (`test_tool_handlers.py` — 18 tests)**
- get_current_time：默认 UTC、命名时区、无效时区
- get_weather：确定性结果、城市映射、空白处理、参数验证

**日志器 (`test_logger.py` — 25 tests)**
- 前缀语法（USER:、ASSISTANT:、TOOL_CALL:、TOOL_RESULT:）
- 会话门控（active 前静默、closed 后静默）
- banner/listening 始终输出
- 非序列化 payload 替换为 `<non-serializable>`
- Unicode 保留

### 3. Web UI（69 个测试）

**WebSocket 消息 (`test_web_messages.py` — 35 tests)**
- 5 种服务器消息类型的构造和序列化
- parse_client_command：有效 start/stop、畸形 JSON、缺失 type、未知 type
- validate_audio_bytes：空字节、奇数长度、有效偶数长度

**WebLogger (`test_web_logger.py` — 15 tests)**
- 继承自 ConsoleLogger
- tool_call/tool_result 通过 send_fn 发送 JSON
- 会话门控抑制
- 非序列化 fallback
- _write 抑制 stdout

**本地 SessionManager (`test_session_manager.py` — 15 tests)**
- 状态机：ready → connecting → active、error 路径
- 音频转发（仅 active 状态 + 验证通过）
- 事件路由（AudioOutEvent → binary、TranscriptEvent → JSON）
- Stop 后回到 ready（可重启）

**会话工厂 (`test_session_factory.py` — 4 tests)**
- local 模式返回 SessionManager
- cloud 模式返回 AgentCoreSessionManager
- 两者初始状态为 ready

### 4. 云部署（67 个测试）

**AgentCore SessionManager (`test_agentcore_session_manager.py` — 25 tests)**
- 状态机：ready → connecting → active、各种 error 路径
- 从 error 状态重试
- 音频转发（仅 active + 验证）
- 事件路由：audio → binary、transcript → JSON、tool_call → JSON、tool_result → JSON
- 流错误 → error 状态
- Stop 关闭流、重置引用、允许重启

**部署配置 (`test_deployment_config.py` — 24 tests)**
- 模式验证（local/cloud + 无效值）
- Cloud 模式必须有 agent_id 和 agent_alias_id
- 空字符串视为 None
- 环境变量加载（默认值、覆盖）
- 冻结 dataclass 不可变性

**Lambda 处理器 (`test_lambda_handlers.py` — 18 tests)**
- 响应格式：messageVersion、actionGroup、apiPath、httpMethod 回显
- 时间处理器：默认 UTC、指定时区、无效时区
- 天气处理器：有效城市、确定性、空白处理、缺失参数
- 与本地工具结果等价性验证
- 未知 API path 返回 unknown_tool

### 5. 属性测试（8 个测试）

**hypothesis 属性测试 (`test_properties.py` — 8 tests)**
- P1: 工具调度器延迟边界
- P2: 调度器结果形状
- P3: get_current_time 形状和时区解析
- P4: get_weather 进程内确定性
- P5: 日志器语法
- P6: 日志器处理非序列化 payload
- P7: 日志器不抛异常
- P8: Session close 幂等性 + logger 关闭后静默

## 测试策略

### 隔离原则
- 所有 AWS 调用通过注入的 mock/fake 替代（FakeRpc、FakeClient、mock boto3 client）
- 音频设备通过注入的 fake `sd` 模块替代
- 无真实网络调用、无真实 AWS 凭证需求

### 属性测试
- 使用 `hypothesis` 库生成随机输入
- 验证系统在任意有效输入下的不变量
- 覆盖：工具调度、日志器、会话生命周期

### 等价性验证
- Lambda 工具处理器与本地工具处理器对相同输入产生相同结果
- 确保云部署不改变业务逻辑

### CI 集成
- `.github/workflows/test.yml` 在每个 PR 上运行 `pytest -q`
- 所有测试必须通过才能合并
