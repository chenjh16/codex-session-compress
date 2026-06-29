# Codex 上下文压缩与 JSONL 物理瘦身分析

本文记录 `codex-session-compress` 对 Codex rollout JSONL 的源码级分析。目标不是讨论 Codex App 加载内存本身，而是回答三个实际问题：

1. 如何尽可能减少 JSONL 文件体积。
2. 如何尽可能完整地保留后续 Agent 恢复时的模型上下文。
3. 如何在 `GOAL_SIZE` 允许时保留 Codex App / `codex resume` 可上翻的旧聊天结构。

源码基准：

- 仓库：`https://github.com/openai/codex`
- 本地源码路径：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src`
- 本次分析 commit：`d2885dc3cdbaf98a60e7256ec3e7dfdf2381041d`
- commit 时间：`2026-06-27T10:47:54-07:00`

注意：公开源码可能与本机 Codex App 内置版本存在小幅差异。本文以本地拉取的公开源码为主，官方手册为辅。

## 结论摘要

用户的判断基本正确，但需要用 Codex 源码中的术语精确化：

> 后续 Agent 从 rollout 恢复模型历史时，会使用最新 full compacted checkpoint 的 `CompactedItem.replacement_history` 作为 history base，然后 replay 该 checkpoint 之后的 `rollout_suffix`。

其中：

- full compacted checkpoint 指 `RolloutItem::Compacted(CompactedItem)`，且 `CompactedItem.replacement_history` 是一个数组。
- 最新 full compacted 之前的原始旧 `ResponseItem`、旧图片/base64、旧截图、旧工具输出，正常情况下不会再以原始形态进入后续模型上下文。
- 旧历史仍可能以 `replacement_history` 内的 summary、被保留的用户消息文本、compaction item 等压缩形态存在。
- 因此，在存在现代 full compacted checkpoint 的 rollout 中，可以物理删除该 checkpoint 之前的大部分历史，而不降低 Codex 源码定义的 resume 后 model-visible history。
- 但 App 可上翻聊天不是同一条模型 history reconstruction 路径。旧聊天 transcript 主要依赖 `EventMsg`；checkpoint 前的旧 `TurnContext` 可作为 UI breadcrumb 优先级的一部分保留。如果只在 checkpoint 前保留旧 `ResponseItem`，App 仍可能只能上翻到 “Context compacted” 标记。

更准确的裁剪边界不是“最新 compacted”，而是“最新带 `replacement_history` 的 compacted”。

## 关键源码证据

### RolloutItem 与 CompactedItem

`RolloutItem` 是 Codex 持久化 session rollout 的顶层枚举，包含 `SessionMeta`、`ResponseItem`、`Compacted`、`TurnContext`、`WorldState`、`EventMsg` 等：

- `/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/protocol/src/protocol.rs:3153`

`CompactedItem` 包含：

- `message`
- `replacement_history: Option<Vec<ResponseItem>>`
- `window_number`
- `first_window_id`
- `previous_window_id`
- `window_id`

对应源码：

- `/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/protocol/src/protocol.rs:3188`

这说明 compacted item 不只是 UI 摘要。现代 full compaction 会把一段可直接替换当前模型 history 的 `replacement_history` 写入 rollout。

### /compact 如何生成 replacement_history

本地 compaction 的核心流程在 `compact.rs`：

1. 克隆当前 `ContextManager` history。
2. 生成 compact summary。
3. 从原 history 中收集用户消息，排除已有 summary。
4. 构造新的 compacted history。
5. 创建 `CompactedItem { replacement_history: Some(new_history.clone()), ... }`。
6. 调用 `replace_compacted_history`，替换内存 history 并持久化 compacted item。

关键源码：

- 生成 `CompactedItem` 并写入 `replacement_history`：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/compact.rs:352`
- 收集用户消息：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/compact.rs:499`
- 构造 compacted history：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/compact.rs:585`
- 安装并持久化 compacted history：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/mod.rs:2975`

一个重要细节：`replacement_history` 不一定只有 summary。Codex 会保留一部分用户消息文本，预算由源码中的用户消息 token 限额控制。因此，`/compact` 已经是语义压缩，而不是原始历史的无损保存。

### resume 如何从 rollout 重建模型 history

恢复 session 时，`Session::record_initial_history` 会调用 `apply_rollout_reconstruction`，后者调用 `reconstruct_history_from_rollout`，并把重建出的 history 安装到 session state：

- `/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/mod.rs:1295`
- `/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/mod.rs:1398`

`reconstruct_history_from_rollout` 的关键逻辑：

1. 从 newest-to-oldest 反向扫描 rollout。
2. 找到最新 surviving `CompactedItem.replacement_history`。
3. 把它设置为 `base_replacement_history`。
4. 把 `rollout_suffix` 设置为该 compacted 行之后的 items。
5. materialize 时先 `history.replace(base_replacement_history.to_vec())`。
6. 再 replay `rollout_suffix` 中会影响 model-visible history 的 item。

关键源码：

- 反向扫描设计说明：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/rollout_reconstruction.rs:113`
- 发现 `replacement_history` 并设置 suffix：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/rollout_reconstruction.rs:181`
- 源码注释说明 older items 不能再影响结果：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/rollout_reconstruction.rs:286`
- 用 replacement history 替换 `ContextManager` history：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/rollout_reconstruction.rs:317`
- replay suffix：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/rollout_reconstruction.rs:325`

测试也验证了这个语义：

- `reconstruct_history_uses_replacement_history_verbatim`：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/core/src/session/tests.rs:1661`

### active suffix 中哪些内容应保留

在 full compacted checkpoint 之后的 suffix 中，应完整保留所有 rollout lines，而不只是 `ResponseItem`：

- `ResponseItem` 会进入 `ContextManager` history。
- `InterAgentCommunication` 可能转换为可见消息。
- `EventMsg::ThreadRolledBack` 会修改历史。
- `TurnContextItem` 和 `WorldStateItem` 会影响恢复后的 reference context、world state baseline 和后续 turn context。
- `EventMsg` 中的 turn started/complete/aborted 会影响 reverse reconstruction 的 segment 边界和 metadata。

因此，checkpoint 之后的 suffix 是必须保留区，不应继续做图片替换、工具输出截断或按 turn 裁剪。

### App / CLI 可上翻聊天如何重建

Codex App 的线程历史展示不是直接使用 `ContextManager` 的 model-visible history。app-server 会从 rollout 构造 `ThreadHistory`，并把适合 UI 的事件转换为可显示 turn。源码路径包括：

- `ThreadHistoryBuilder` 入口：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/app-server/src/request_processors.rs:619`
- thread history 协议与 builder：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/app-server-protocol/src/protocol/thread_history.rs`
- rollout replay 中 `EventMsg` 会进入 `handle_event`，`TurnContext` 会被忽略，`ResponseItem` 只走 `handle_response_item`：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/app-server-protocol/src/protocol/thread_history.rs:384`
- 普通 user `ResponseItem` 不会成为 UI turn 的测试：`/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src/codex-rs/app-server-protocol/src/protocol/thread_history.rs:3829`

这条路径更依赖旧 rollout 中的 `EventMsg` 结构。普通 user/assistant `ResponseItem` 对模型历史有意义，但不等价于 App transcript。源码测试也覆盖了普通 user `ResponseItem` 在 rollout replay 中不会直接变成 UI turn 的情况。因此：

- 为模型续作，必须保留最新 full `CompactedItem.replacement_history` 和之后的 `rollout_suffix`。
- 为 App 上翻旧聊天，在 `GOAL_SIZE` 允许时，应额外保留 checkpoint 之前两端的 `event_msg`，并按用户目标把 checkpoint 前 `turn_context` 放在同一 UI breadcrumb 优先级中尽量保留。
- 只保留 checkpoint 前 `response_item` breadcrumb 会让 raw 细节存在于 JSONL，但可能无法恢复 App 旧聊天可见性。

## 对用户判断的精确回答

原判断：

> 后续 Agent 的模型上下文，应该只包含最新 compacted 检查点以及它之后的内容，而最新 compacted 之前遗留的旧历史、图片/base64/截图、工具输出不会再进模型上下文。

精确版：

> 后续 Agent 的模型上下文会以最新 full compacted checkpoint 的 `replacement_history` 作为 history base，再叠加该 checkpoint 之后的 `rollout_suffix`。checkpoint 之前的原始旧历史不会再以原始 `ResponseItem` / 图片 / 工具输出的形式进入模型上下文。

这个判断成立的条件：

1. rollout 中存在至少一个 `payload.replacement_history` 为数组的 `compacted`。
2. 裁剪时保留最新的这条 full compacted 行。
3. 裁剪时完整保留该行之后的所有 rollout lines。
4. 裁剪后仍保留第一条 `session_meta`，使 Codex 能识别 session。
5. 不把 checkpoint 之后的 active suffix 做图片替换或输出截断，除非用户明确愿意牺牲最新上下文。

不安全或需要降级的情况：

- 没有 full compacted checkpoint。
- 最新相关 compacted 没有 `replacement_history`。
- JSONL 有损坏行，无法可靠定位 checkpoint。
- checkpoint 后的 suffix 自身已经过大；此时进一步缩小会触碰真实后续上下文。
- 只保留 `ResponseItem`，丢掉 checkpoint 后的 `TurnContext`、`WorldState`、rollback 等事件。

## 本地案例：019f0474-d2f7-77e3-ae06-65f1510a6d77

目标 rollout：

```text
/Users/chenjh/.codex/sessions/2026/06/26/rollout-2026-06-26T23-03-12-019f0474-d2f7-77e3-ae06-65f1510a6d77.jsonl
```

统计结果：

```text
原始大小                 3,982,516,388 bytes / 3798.0 MiB
总行数                   146,799
compacted 数量           223
full compacted 数量      223
最新 full compacted 行    145,901
最新 checkpoint 前历史    约 3.681 GiB
最新 checkpoint 行大小    约 0.098 MiB
checkpoint 后 suffix      约 28.666 MiB
replacement_history      37 items / 约 102,620 bytes
```

按“第一条 `session_meta` + 最新 full compacted + 之后所有行”裁剪后的临时结果：

```text
压缩后大小               28.8 MB
行数                     900
最大单行                 2.6 MB
compacted checkpoint     存在
function_call/output     209 / 209，按 call_id 配对
active suffix 图片        18 个，保留
```

这些 18 个 image parts 位于最新 full compacted 之后，属于 active suffix。对“语义尽量无损”的目标来说，它们不应默认替换为文本。

## 对 codex-session-compress 的设计含义

推荐默认策略固定为语义优先的 checkpoint strategy：

```text
semantic checkpoint strategy:
  keep first SessionMeta
  keep newest full CompactedItem where replacement_history is present
  keep every RolloutItem after that checkpoint
  use remaining GOAL_SIZE budget first for two-ended original EventMsg plus pre-checkpoint TurnContext breadcrumbs before checkpoint
  use any leftover budget for two-ended original ResponseItem raw-detail breadcrumbs before checkpoint
  insert one localized synthetic maintenance EventMsg turn (`task_started` -> `user_message` -> `agent_message` -> `task_complete`) at the pre-checkpoint elision gap
```

这个策略同时满足三个目标：

- JSONL 物理体积显著下降，因为旧 checkpoint 前无法放入 `GOAL_SIZE` 预算的中间历史会被直接删除。
- 在 `GOAL_SIZE` 允许时，旧 checkpoint 前的原始 `EventMsg` 会从两端交替优先保留，并一起保留 checkpoint 前 `TurnContext` 结构，让 Codex App / `codex resume` 更有机会恢复可上翻聊天。
- 如果 UI breadcrumb 后还有预算，旧 checkpoint 前的原始 `ResponseItem` 会从两端交替尽量保留，包括消息、reasoning、工具调用、工具输出和带图片的 response item；这些是历史 raw-detail breadcrumb，不会替代最新 full checkpoint 的 `replacement_history`。
- 模型上下文尽量无损，因为保留了 Codex resume reconstruction 真正使用的 `replacement_history` 和完整 `rollout_suffix`。
- elision marker 的唯一规范是显式 synthetic maintenance turn：`event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete`。它必须插在 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置，并且 synthetic `user_message.client_id` 必须使用 `codex-session-compress-elision-` 前缀。注入文案按操作系统语言选择，中文系统写中文，其它语言默认英文。因为 `EventMsg::UserMessage` 在源码中会作为 user turn boundary，验证器必须拒绝任何位于最新 full checkpoint 之后的 synthetic marker，并要求 synthetic maintenance turn 是完整连续的四事件结构；正常 resume 仍由最新 full checkpoint 的 `replacement_history` 和完整 `rollout_suffix` 驱动。
- 二次压缩已经含有旧 `codex-session-compress` marker 的 rollout 时，checkpoint 前旧 marker 事件应从 breadcrumb 候选中省略；输出只保留本次压缩产生的当前四事件 synthetic maintenance turn，避免旧 marker fragment 干扰 App history reconstruction 或 verifier。
- 压缩完成条件包括 Codex CLI/app-server confirmation：通过 `codex app-server --stdio` 的只读 `thread/read includeTurns=true` 确认压缩后的 rollout 能被 Codex 自己重建为 thread history，并且 synthetic maintenance turn 的 synthetic `userMessage` / `agentMessage` 已进入同一个可见 reconstructed turn。

对目标 session，默认 `GOAL_SIZE=100MB` 时，mandatory segment 约 28.8MB，小于目标值，因此还可以在目标预算内额外保留少量历史 breadcrumb；如果只做纯 checkpoint cut，则文件约 28.8MB。

## 术语表

- `RolloutItem`：Codex JSONL 中每一行反序列化后的顶层 item。
- `SessionMeta`：记录 session id、cwd 等 metadata 的 rollout item。物理裁剪后必须保留在首行。
- `ResponseItem`：模型 API history 相关 item，包括 user/assistant message、function call、function output 等。
- `EventMsg`：UI 和事件流相关 item，是 App 可上翻聊天的重要来源之一。
- `CompactedItem`：compaction checkpoint。现代 full compaction 会包含 `replacement_history`。
- `replacement_history`：compact 后用于替换 `ContextManager` history 的 `Vec<ResponseItem>`。
- `rollout_suffix`：最新 full compacted 之后的 rollout items。resume 时会在 replacement history 之上 replay。
- `ContextManager`：Codex core 中维护 model-visible history 的结构。
- `TurnContextItem`：恢复 turn context/reference context 的持久化 item。
- `WorldStateItem`：恢复 world state baseline/patch 的持久化 item。
- `EventMsg::ThreadRolledBack`：会影响 history replay 的 rollback 事件。
- `InterAgentCommunication`：可能转换为 message content 的 agent 间通信 item。
