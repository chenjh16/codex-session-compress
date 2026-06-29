# Troubleshooting

本文档记录使用 `codex-session-compress` 时常见问题和处理方式。

## 找不到 session ID

症状：

```text
error: no rollout found for session id ... under ~/.codex/sessions
```

处理：

1. 先运行：

   ```bash
   python scripts/list_rollouts.py --top 0 --show-cwd
   ```

2. 确认 ID 是否完整 UUID。
3. 如果文件名被改过，使用元数据扫描：

   ```bash
   python scripts/compress_session_by_id.py <session-id> --scan-meta --dry-run
   ```

4. 如果 Codex 使用了自定义 `CODEX_HOME`，可先设置环境变量或复制文件路径后直接运行 `repair_rollout.py <path>`。

## 压缩后 verify 失败

症状：

```text
VERDICT: NOT safe - address the FAIL(s) above
```

处理：

- 如果脚本自动检测到备份，默认会尝试恢复。
- 如果没有自动恢复，请手动恢复：

  ```bash
  mv '<backup-path>' '<rollout-path>'
  ```

- 检查失败原因：坏 JSON、缺少 `session_meta.id`、缺少 full compacted checkpoint、synthetic maintenance turn 不是完整四事件结构、active suffix 函数调用不配对、图片策略不匹配、文件仍大于上限。

## 仍然有图片残留

可能原因：

- 使用 semantic checkpoint cut 时，图片可能位于最新 full checkpoint 的 `replacement_history`、checkpoint 之后的 active suffix，也可能是 `GOAL_SIZE` 预算内保留下来的 historical breadcrumb（`event_msg` / `turn_context` / `response_item`）。这些情况都是允许的，验证应使用 `--semantic-checkpoint --allow-active-images`。其它位置的图片仍会失败。
- JSON 字段形态不是常见的 `input_image` / `image.data` / `data:image...`。

处理：

```bash
python scripts/repair_rollout.py <rollout-path> --goal-size 100MB
python scripts/verify_rollout.py <rollout-path> --semantic-checkpoint --allow-active-images --require-full-compacted
```

如果仍失败，需要人工检查包含 `image`、`base64`、`mimeType` 的行，确认这些图片是否属于 latest full checkpoint、active suffix 或预算内保留的 historical breadcrumb。

## 文件已经小于 target，但还是打不开

可能原因：

- 文件 JSONL 结构损坏。
- `session_meta` 不存在或 ID 丢失。
- 最新 full `compacted` 内容本身有问题，或没有 `replacement_history`。
- Codex 客户端仍持有旧状态。

处理：

1. 运行：

   ```bash
   python scripts/verify_rollout.py <rollout-path>
   ```

2. 关闭所有 Codex 进程后再重新打开。
3. 如果有备份，恢复备份后用更保守参数重新修复。

## Codex CLI 检查卡住

默认压缩后的 CLI confirmation 使用的是非交互、只读检查：

```bash
python scripts/confirm_session_with_codex_cli.py <session-id> --require-synthetic-marker
```

它启动 `codex app-server --stdio`，只发送 `initialize` 和 `thread/read includeTurns=true`，不会追加 turn 或消耗模型调用。若这里失败，通常说明 Codex 自己无法重建压缩后的 thread history，或者 synthetic `userMessage` / `agentMessage` 没有进入同一个可见 reconstructed turn；默认 wrapper 会恢复 `.orig` 备份。

`--codex-check resume` 是额外人工检查，会运行交互式：

```bash
codex resume <session-id>
```

它需要用户手动退出 Codex。不要把它当作非交互 CI 检查。如果需要完全自动化的 prompt 检查，应先获得用户确认，因为这可能追加新 turn 并消耗使用额度。

## 二次压缩后 synthetic marker 校验失败

可能原因：

- 压缩前的旧历史中已经存在旧版 `codex-session-compress` 标记。
- 旧标记被当作普通 breadcrumb 保留下来，但不是当前要求的完整四事件 synthetic maintenance turn。

处理：

1. 确认使用当前版本脚本。当前 `repair_rollout.py` 会在二次压缩时省略 checkpoint 前的旧 marker 事件。
2. 重新压缩并查看 JSON 结果中的 `historical_compression_marker_events_omitted`。
3. 再运行：

   ```bash
   python scripts/verify_rollout.py <rollout-path> --semantic-checkpoint --allow-active-images
   python scripts/confirm_session_with_codex_cli.py <session-id> --require-synthetic-marker
   ```

## 什么时候可以删除备份

只有在以下条件都满足时才删除：

1. `verify_rollout.py` 显示 `safe to load`。
2. `confirm_session_with_codex_cli.py` 或 wrapper 内置 Codex CLI confirmation 成功，且 synthetic 压缩消息已在 Codex 重建 history 的同一个可见 turn 中可见。
3. 用户已经用 Codex 打开该会话并确认能继续工作。
4. 用户明确要求删除备份。

脚本提供备份列表，方便用户确认路径：

```bash
python scripts/compress_session_by_id.py <session-id> --list-backups
```

删除前先 dry-run：

```bash
python scripts/cleanup_compression_backups.py <session-id>
python scripts/cleanup_compression_backups.py --all
```

确认无误后再执行：

```bash
python scripts/cleanup_compression_backups.py <session-id> --apply --yes
python scripts/cleanup_compression_backups.py --all --apply --yes
```

该脚本只清理 sessions 目录下匹配 `rollout-*.jsonl.orig` / `rollout-*.jsonl.orig.N` 的压缩备份，不清理 active rollout、SQLite state 或 SubAgent cleanup manifest。

## 什么时候可以清理 SubAgent

可以清理的前提：

1. 显式请求的 root session 能从 `session_meta`、SQLite `threads` 元数据或 `thread_spawn_edges` 识别为 SubAgent。
2. dry-run 显示 `thread_spawn_edges.status = closed`。
3. root SubAgent 及其 spawned descendants 的关键结论已经回到父会话，用户不再需要打开这些子会话查看完整历史。
4. 用户已经看过 dry-run 计划并确认删除范围。

预览：

```bash
python scripts/cleanup_session_by_id.py <session-id>
```

执行：

```bash
python scripts/cleanup_session_by_id.py <session-id> --apply --yes
```

dry-run 会显示请求的 root IDs、canonical state DB、自动展开的 descendant IDs，以及最终 cleanup IDs。确认时要同时确认 descendants 也可删除。secondary state DB 中额外看到的 descendants 只会作为 `stale_spawn_descendant_candidates` 诊断输出，不会被实际清理。

如果 dry-run 显示 `refused_status_conflict`，说明多个 `state_*.sqlite` 对显式请求 root 的 spawn edge 状态不一致。不要直接 `--apply`；先确认 Codex 是否仍在运行、是否存在旧 state DB，再决定是否手动指定状态目录或清理旧库。

只有确认冲突来自 stale secondary state DB，且用户明确要求继续清理这些 SubAgent 时，才追加：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-status-conflict --apply --yes
```

如果 `--apply` 报 Codex 仍在运行，推荐关闭使用目标 Codex home 的 Codex 后重试。脚本会尽量读取进程环境中的 `CODEX_HOME`；未声明 home 的 Codex 进程会按默认 `~/.codex` 处理。只有确认目标 session、父会话和 state DB 不会被活跃进程改写时，才使用：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-running-codex --apply --yes
```

如果 dry-run 对显式请求的 root 显示 `NOT subagent`，脚本会拒绝清理。只有用户明确要求删除普通 root session 时才使用：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-non-subagent --apply --yes
```

如果 dry-run 对显式请求的 root 显示 SubAgent 但状态为 `open` 或 `unknown`，默认也会拒绝。只有用户确认该 root 子会话可丢时才使用：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-open-subagent --apply --yes
```

如果用户说“清理超时 SubAgent”，应先用超时规则而不是直接放开 open/unknown：

```bash
python scripts/cleanup_session_by_id.py <subagent-id...> \
  --allow-timeout-subagent \
  --timeout-hours 12 \
  --json
```

dry-run 中 `plan.sessions[*].timeout_subagent` 会列出父会话、父/子 last-active 时间和相差小时数。正式执行时只清理非 refused 的 ID；未达到阈值的 open/unknown SubAgent 应继续保留。

清理前建议关闭 Codex。清理后如果 App 仍显示旧条目，重启 Codex 让列表重新读取本地文件和 SQLite 状态。

如果成功清理后需要回滚，使用 manifest 恢复工具：

```bash
python scripts/restore_cleanup_manifest.py <cleanup-manifest.json>
python scripts/restore_cleanup_manifest.py <cleanup-manifest.json> --apply --yes
```

## 避免再次膨胀

- 不要无限期在单个会话中进行大量 browser/computer-use 截图操作。
- 大任务分阶段处理，关键结论写入项目文档或 `AGENTS.md`。
- 定期运行 `list_rollouts.py` 查看异常增长的会话。
- 对非常大的输出，要求 agent 摘要结果而不是粘贴完整日志。
