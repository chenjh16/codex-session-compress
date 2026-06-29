# Codex rollout JSONL 格式说明

本文档说明本项目处理的 Codex 本地会话 rollout JSONL 的基本结构，以及哪些内容可以安全压缩。

## 文件位置

Codex 会话通常保存在：

```text
~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl
```

每一行是一个 JSON 对象。文件名最后的 `<session-id>` 通常与第一条 `session_meta` 记录中的 `payload.id` 一致。

## 常见记录类型

| type | 作用 | 是否参与模型重放 |
| --- | --- | --- |
| `session_meta` | 会话元数据，包含 `id`、`timestamp`、`cwd` 等。必须存在。 | 不直接参与，但加载必须依赖它 |
| `response_item` | 对话消息、reasoning、tool call、tool output 等。 | 是 |
| `compacted` | `/compact` 生成的压缩检查点。带 `replacement_history` 的最新 full checkpoint 会成为恢复基线。 | 最新 full checkpoint 是 |
| `turn_context` | 每轮模型、cwd、配置等上下文；checkpoint 前会放在 UI breadcrumb 优先级中随旧 `event_msg` 尽量保留。 | 间接影响 |
| `event_msg` | UI 和事件流记录，例如用户消息、agent 消息、工具结束事件、token 统计等。 | 通常不进入模型 history，但影响 App 可上翻聊天 |

## 最容易膨胀的内容

### 1. 图片和截图

`computer-use` 或浏览器工具可能把截图写入 rollout，常见形态包括：

```json
{"type":"input_image","image_url":"data:image/jpeg;base64,..."}
```

以及事件流中的 MCP 图片块：

```json
{"type":"image","data":"<base64>","mimeType":"image/jpeg"}
```

长会话中这类图片可能占据大部分体积。本 skill 不再对图片做文本替换；如果图片位于最新 full checkpoint 的 `replacement_history` 或该 checkpoint 之后，它们属于保留的语义基底或 active suffix，必须保留。checkpoint 之前的旧图片如果被 `GOAL_SIZE` 预算选中，会作为原始 historical breadcrumb 保留；breadcrumb 优先级是 `event_msg` 与 checkpoint 前 `turn_context` 优先，剩余预算再保留 `response_item` 细节。预算放不下的中间旧图片会随旧历史中段被物理裁掉。

### 2. 过期 compacted 检查点

每次 compact 可能写入新的 `compacted` 记录。现代 full checkpoint 的判定标准是 `payload.replacement_history` 为数组。恢复时会以最新 full checkpoint 的 `replacement_history` 为 base history，再 replay 该 checkpoint 之后的 suffix。旧 checkpoint 前的大量原始历史通常可以物理裁掉。

### 3. 超大工具输出

某些 shell、日志、构建输出可能非常大。本 skill 不再单独截断工具输出；checkpoint 之后的输出属于 active suffix，必须保留。checkpoint 之前的旧输出如果被 `GOAL_SIZE` 预算选中，会作为原始 historical breadcrumb 保留；预算放不下的中间旧输出会随旧历史中段被物理裁掉。

## 语义优先压缩顺序

1. 找到最新带 `replacement_history` 的 full `compacted`。
2. 保留第一条 `session_meta`、该 full checkpoint、以及 checkpoint 后所有 rollout 行。
3. 在 `GOAL_SIZE` 剩余预算内，从 checkpoint 前旧历史的两端交替优先保留原始 `event_msg`，并把 checkpoint 前 `turn_context` 放在同一 UI breadcrumb 优先级中尽量保留，以保留 App 可上翻聊天结构。
4. 如果仍有预算，再从两端交替保留原始 `response_item` breadcrumb。
5. 用一个显式 synthetic maintenance turn 表示中间被截去的旧历史：依次写入 `event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete`，并把它放在 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置。synthetic `user_message.client_id` 使用 `codex-session-compress-elision-` 前缀；文案按操作系统语言选择中文或英文，并说明它是压缩工具写入的标记，不是原始用户指令。
6. 如果没有 full checkpoint，停止并拒绝修改文件。

二次压缩时，如果 checkpoint 前旧历史里已经有旧版或上一轮 `codex-session-compress` synthetic marker，这些 marker 事件不再作为 breadcrumb 候选保留。当前输出只写入一个新的、完整连续的四事件 synthetic maintenance turn，避免旧 marker fragment 被拆碎后影响 App 可见历史或 verifier 校验。

## 不变量

- 必须保留 `session_meta.id`。
- 必须保留最新 full `compacted` 记录；不能只按最新 `type == "compacted"` 判断。
- 必须完整保留最新 full checkpoint 之后的 suffix。
- latest full checkpoint 本行的 `replacement_history` 图片属于 checkpoint 基底，验证时允许保留。
- synthetic maintenance turn 必须是完整连续的 `event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete` 四事件结构，并且必须位于最新 full checkpoint 之前。
- 旧的 `codex-session-compress` synthetic marker 不能被当作普通历史 breadcrumb 继续保留；二次压缩结果应只包含本次物理 elision 对应的当前 marker。
- 不要替换 active suffix 中的图片，除非用户明确接受语义损失。
- 不要使用非 checkpoint 的旧 turn 裁剪。
- 压缩后必须运行 `verify_rollout.py`。

## 为什么要控制文件大小

Codex 桌面端或相关加载器可能把整个 rollout 聚合到单个内存字符串。V8 单字符串存在约 512 MB 的上限。本项目默认 `GOAL_SIZE=100MB`；如果 mandatory checkpoint segment 已经超过目标值，结果允许超过目标值，因为不能为了体积删除 active model context。
