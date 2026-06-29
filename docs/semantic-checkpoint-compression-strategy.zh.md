# 语义优先 JSONL 压缩策略设计

本文是 `codex-session-compress` 的新压缩策略规范。策略目标是同时满足：

1. 尽可能减少 Codex rollout JSONL 的物理体积。
2. 尽可能完整地保留后续 Agent resume 时的模型上下文。
3. 在 `GOAL_SIZE` 允许时，尽可能保留 Codex App / `codex resume` 可上翻的旧聊天结构。

## 策略名称

推荐名称：

```text
semantic checkpoint cut
```

脚本参数：

```bash
python scripts/repair_rollout.py <rollout> --goal-size 100MB
python scripts/repair_rollout.py <rollout> --goal-size 80MB
python scripts/compress_session_by_id.py <session-id> --goal-size 1GB
```

这是唯一压缩策略。必须存在 full compacted checkpoint；不存在时脚本直接失败并拒绝修改文件。

## GOAL_SIZE

`GOAL_SIZE` 是压缩目标大小，默认：

```text
100MB
```

脚本接受：

- `100MB`
- `80M`
- `1GB`
- `1.5GiB`
- `900KB`
- 纯数字，例如 `100`，按 MiB 解释。

skill 在自然语言中应这样映射：

```text
“压到 80MB”       -> --goal-size 80MB
“目标 1GB”        -> --goal-size 1GB
“默认压缩”        -> --goal-size 100MB
“尽量保守但小于 500MB” -> --goal-size 500MB
```

`GOAL_SIZE` 不是硬性上限。必须保留的语义区域如果已经超过 `GOAL_SIZE`，结果允许超过目标值，因为不能为达标而删除 active model context。

## Full Compacted Checkpoint

安全裁剪点定义为最新 full compacted checkpoint：

```text
RolloutItem::Compacted(CompactedItem)
where CompactedItem.replacement_history is Some(Vec<ResponseItem>)
```

不能只看 `type == "compacted"`。没有 `replacement_history` 的 compacted 可能依赖旧 history 辅助重建，不能作为安全物理裁剪点。

## Mandatory Segment

以下内容必须完整保留，不受 `GOAL_SIZE` 限制：

1. 第一条 `SessionMeta`。
2. 最新 full `CompactedItem` 行。
3. 最新 full compacted 之后的所有 `RolloutItem` 行。

对应源码语义：

- `replacement_history` 会成为 resume 后 `ContextManager` 的 base history。
- checkpoint 之后的 `rollout_suffix` 会 replay 到 base history 之上。
- suffix 中的 `TurnContextItem`、`WorldStateItem`、`EventMsg::ThreadRolledBack`、`InterAgentCommunication` 都可能影响恢复状态或 model-visible history。

因此，mandatory segment 不允许做以下操作：

- 图片/base64 转文本。
- 工具输出截断。
- 按 turn 裁剪。
- 只保留 `ResponseItem` 而删除其它 rollout item。

## Optional Historical Breadcrumb

最新 full compacted 之前的旧 rollout 不再作为 active resume 的原始模型上下文使用，但 `GOAL_SIZE` 的目标不是一味压到最小，而是在安全保留 mandatory segment 之后，尽量保留旧历史的原始结构，便于 App 上翻、human-readable trace、故障排查和 UI continuity。

候选范围：

```text
from first visible history item (usually turn_context/event_msg near the first user message)
to the line before newest full compacted checkpoint
```

候选 item 分两层：

```text
priority 1: RolloutItem::EventMsg + pre-checkpoint RolloutItem::TurnContext
priority 2: RolloutItem::ResponseItem
```

`event_msg` 是 Codex App 和 app-server 重建聊天 transcript、turn boundary、上下文压缩提示等 UI 历史时更重要的行。旧 `turn_context` 在 app-server `ThreadHistoryBuilder` 中不是主要 transcript 来源，但会放在同一 UI breadcrumb 优先级中按用户目标一起保留。只保留旧 `response_item` 虽然对 raw 模型细节有帮助，但普通 user/assistant `response_item` 不一定会被 App history builder 转换成可上翻聊天。因此 optional breadcrumb 先保留 UI-visible 事件结构，再使用剩余预算保留 raw `response_item` 细节。

这里的 breadcrumb 都是原始行级保留，不做文本化改写；当预算允许时，`response_item` 可以保留旧消息、reasoning、function call、function call output、custom tool call/output、web/tool search item、image generation item，以及带图片的 message。达到 `GOAL_SIZE` 后，无法保存的中间候选直接丢弃，不做摘要替换，并用显式 synthetic maintenance turn 标记。

旧 `CompactedItem` 和旧 `WorldStateItem` 默认不进入 optional breadcrumb。它们对最新 full checkpoint 之后的 model reconstruction 没有作用，且可能体积很大。checkpoint 之后的这些 item 属于 mandatory suffix，必须完整保留。

### 当 Mandatory Segment > GOAL_SIZE

如果必须保留区本身已经超过目标大小：

1. 完整保留 mandatory segment。
2. 在 checkpoint 前只额外保留第一条用户发起任务的 App-visible `event_msg`，如果没有则保留第一条用户 `ResponseItem`。
3. 插入一个显式 synthetic maintenance turn，说明旧历史中间内容已截去。该 turn 放在 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置；它必须早于最新 full checkpoint。

原因：

- 不能删除 mandatory segment。
- 第一条用户消息通常是整个任务的原始意图，保留它有利于人类审计和 UI continuity。
- 额外保留更多旧历史会让文件更大，却不会改善 Codex resume 后的 active model context。

### 当 Mandatory Segment <= GOAL_SIZE

如果必须保留区小于目标大小：

1. 计算剩余预算：

```text
remaining = GOAL_SIZE - mandatory_segment_size - placeholder_budget
```

2. 从 optional historical range 的两端交替保留 `event_msg` 以及 checkpoint 前 `turn_context`：

```text
head[0], tail[-1], head[1], tail[-2], ...
```

3. 每条 item 只有在加入后不超过预算时才保留。
4. 如果还有剩余预算，再用同样的两端交替方式保留 `response_item`。
5. 被省略的中间历史用一个显式 synthetic maintenance turn 表示：`event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete`。该 turn 插入到 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置，绝不插入到 checkpoint 后的 active suffix。

这种方式保留：

- 最早的用户任务意图。
- 最接近 full compaction 的旧 UI transcript 尾部。
- 预算允许时的旧 raw model details。

同时删除：

- 中间最占空间、且已经被 `replacement_history` 摘要覆盖的旧详细记录。

## Synthetic Maintenance Turn 设计

synthetic maintenance turn 使用合法 JSONL 行，且模拟一个完整的显式 turn：

```json
{"timestamp":"2026-06-29T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"codex-session-compress-elision-203157","model_context_window":null,"collaboration_mode_kind":"default"}}
{"timestamp":"2026-06-29T00:00:00.000Z","type":"event_msg","payload":{"type":"user_message","client_id":"codex-session-compress-elision-203157","message":"[codex-session-compress 合成标记] 此处有一段旧历史 rollout 因压缩被省略。这条用户气泡由压缩工具写入，用于标记可见历史的截断位置，不是原始用户指令。","images":[],"local_images":[],"text_elements":[]}}
{"timestamp":"2026-06-29T00:00:00.000Z","type":"event_msg","payload":{"type":"agent_message","message":"codex-session-compress 已省略 ...","phase":"commentary","memory_citation":null}}
{"timestamp":"2026-06-29T00:00:00.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"codex-session-compress-elision-203157","last_agent_message":"codex-session-compress 已省略 ..."}}
```

注入文案按操作系统语言选择：macOS 优先读取 `AppleLocale`，再回退 `LC_ALL` / `LC_MESSAGES` / `LANGUAGE` / `LANG`；中文系统写中文，其它语言默认英文。它被插入到 checkpoint 前的 elision gap。它的作用是：

- 让人类看到历史曾被物理裁剪。
- 给出物理省略的 checkpoint 前 rollout line count、omitted bytes、checkpoint line、mandatory size、goal size；breadcrumb 候选省略数与物理省略行数分开统计。
- 避免 JSONL 出现不可解析的注释或非标准行。
- 用用户气泡更醒目地标记“这里发生了历史截断”，同时明确说明它不是原始用户指令。

位置规则：

```text
session_meta
retained left-side historical breadcrumb(s)
...
elision synthetic maintenance turn
...
retained right-side historical breadcrumb(s)
newest full compacted checkpoint
event_msg.context_compacted
mandatory suffix
```

synthetic maintenance turn 必须保持完整连续的四行结构，并且必须位于最新 full checkpoint 之前。`event_msg.user_message` 在 Codex 源码中会被当作用户 turn boundary，因此验证器必须拒绝任何出现在最新 full checkpoint 之后的 synthetic compression marker，也必须拒绝缺少 `task_started -> user_message -> agent_message -> task_complete` 任一环节或 ID 不一致的 synthetic turn，避免 synthetic user_message 进入 active suffix。正常 resume 仍以最新 full `CompactedItem.replacement_history` 和其后的 `rollout_suffix` 为模型历史基底。

## 验证策略

旧 verifier 的“无图片残留”规则不适合 semantic checkpoint cut，因为 latest full checkpoint 的 `replacement_history` 与 active suffix 中的图片都可能是后续模型上下文的一部分。

新验证模式：

```bash
python scripts/verify_rollout.py <rollout> \
  --require-compacted \
  --require-full-compacted \
  --semantic-checkpoint \
  --allow-active-images
```

检查项：

1. 每一行都是合法 JSON。
2. `SessionMeta` 存在。
3. 至少有一个 `compacted`。
4. 至少有一个 full compacted checkpoint。
5. 文件低于 V8 single-string ceiling。
6. active suffix 中的 `function_call` / `function_call_output` `call_id` 状态会被报告；普通缺失 output 或孤儿 output 不再阻断，因为 Codex 源码中的 `normalize_history` 会在模型输入前插入 `aborted` output 或移除孤儿 output。
7. 使用 semantic compression 时，图片如果位于 latest full checkpoint、active suffix 或预算内保留下来的 historical breadcrumb（`event_msg` / `turn_context` / `response_item`），允许保留并报告数量；其它位置的图片仍然失败。
8. synthetic compression marker 必须位于最新 full checkpoint 之前，避免 synthetic `userMessage` 进入 active suffix。

需要机器可读结果时可追加：

```bash
python scripts/verify_rollout.py <rollout> \
  --semantic-checkpoint \
  --allow-active-images \
  --json
```

`--semantic-checkpoint` 的 active segment 定义：

```text
all rollout lines after newest full compacted checkpoint
```

静态 verifier 通过后，还必须做 Codex CLI confirmation：

```bash
python scripts/confirm_session_with_codex_cli.py <session-id> \
  --require-synthetic-marker
```

该脚本通过 `codex app-server --stdio` 发送只读 `thread/read includeTurns=true`，验证 Codex 自己能把压缩后的 rollout 重建为 thread history。含 synthetic maintenance turn 的压缩结果必须在同一个 reconstructed thread turn 中同时满足：

1. 可见 items 中存在 `userMessage.clientId` 以 `codex-session-compress-elision-` 开头的 synthetic user bubble。
2. 可见 items 中存在文案包含 `codex-session-compress` 的 synthetic agent message。

这个检查不发送 `thread/resume`、`turn/start` 或 prompt，因此不应追加新 turn，也不消耗模型调用。`compress_session_by_id.py` 在真实压缩并通过静态 verifier 后默认运行该检查；失败时应恢复 `.orig` 备份。

## 压缩备份生命周期

真实压缩必须保留可回滚备份。当前生命周期是：

1. `repair_rollout.py` 写好临时瘦身 JSONL 后，在同一文件系统内把原 rollout rename 为 `rollout-*.jsonl.orig` / `rollout-*.jsonl.orig.N`，再安装压缩后的 JSONL。
2. `verify_rollout.py` 与 `confirm_session_with_codex_cli.py` 都通过后，备份仍然保留。
3. 用户确认压缩结果可用并明确要求清理备份时，先 dry-run：

   ```bash
   python scripts/cleanup_compression_backups.py <session-id>
   python scripts/cleanup_compression_backups.py --all
   ```

4. 确认删除范围后，才执行：

   ```bash
   python scripts/cleanup_compression_backups.py <session-id> --apply --yes
   python scripts/cleanup_compression_backups.py --all --apply --yes
   ```

`cleanup_compression_backups.py` 只匹配 sessions 目录下的 `rollout-*.jsonl.orig` / `rollout-*.jsonl.orig.N` 压缩备份，不清理 active rollout、SQLite state、SubAgent cleanup manifest 或其它用户文件。

## 策略取舍

策略优点：

- 直接贴合 Codex resume reconstruction。
- 不破坏最新 checkpoint 后的真实上下文。
- 对已有 full compaction 的大型 session，通常能把无法放入 `GOAL_SIZE` 预算的旧中段历史物理裁掉；预算内选中的旧 `event_msg` 以及 checkpoint 前 `turn_context` 会以原始形态优先保留，用于 App 上翻；剩余预算内的旧 `response_item` 也会以原始形态保留，包括旧图片和旧工具输出。

策略限制：

- mandatory suffix 如果很大，不能继续无损压小。
- 为了在 `GOAL_SIZE` 内尽量保留原始旧历史，结果可能包含 checkpoint 前的旧 UI 事件、旧工具输出或旧图片；这属于设计目标，不代表它们会重新参与 active resume reconstruction。
- 没有 full `replacement_history` 的 rollout 不能使用；请先在 Codex 中运行 `/compact`。

## 目标案例预期

对 session：

```text
019f0474-d2f7-77e3-ae06-65f1510a6d77
```

原始 rollout：

```text
约 3798.0 MiB
```

latest full checkpoint：

```text
line 145901
replacement_history: 37 items / 约 100 KiB
suffix after checkpoint: 约 28.666 MiB
```

使用默认：

```bash
python scripts/repair_rollout.py <rollout> --goal-size 100MB --output <tmp.jsonl>
```

预期：

- 结果小于 100MB。
- 完整保留 latest full `replacement_history` 和 suffix。
- 优先保留第一条用户任务消息、checkpoint 前尾部若干 `event_msg` 以及 checkpoint 前 `turn_context`，让 App 在预算允许时可以上翻旧聊天。
- 预算有余时再保留 checkpoint 前两端的若干 `response_item`。
- 中间旧历史由显式 synthetic maintenance turn 表示，且该 turn 应位于 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置。
- active suffix 图片保留。
