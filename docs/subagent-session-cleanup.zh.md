# SubAgent Session 清理方案

本文说明如何按 root `session_id` 清理已完成 SubAgent 及其 spawned descendants 的本地残留。目标是减少本地 rollout 和状态库占用，同时避免误删普通主会话。

## 源码依据

本方案参考本地 `openai/codex` checkout：

```text
/Users/chenjh/Documents/Codex/2026-06-27/co/openai-codex-src
commit d2885dc3cdbaf98a60e7256ec3e7dfdf2381041d
```

关键实现：

- `codex-rs/state/src/runtime/threads.rs`：`thread_spawn_edges.status` 存储 spawn edge lifecycle，可见 `Open` / `Closed` 状态；`delete_threads_strict` 会清理 logs、memories、thread goals、dynamic tools、spawn edges 和 threads；如果 agent job runner 与 worker 都在待删除集合中，会取消相关 `agent_jobs`；并解除 `agent_job_items.assigned_thread_id`。
- `codex-rs/app-server/tests/suite/v2/thread_delete.rs`：删除 root thread 时会连带删除 spawned descendants；测试覆盖 closed child 和 open grandchild。
- `codex-rs/rollout/src/recorder.rs` 与 app-server request processors：rollout 与 app state 分属文件和 SQLite，本 skill 的清理脚本需要同时处理二者。

因此，一个已经完成且不再需要打开的 SubAgent，可以从本地 sessions/archived_sessions、`session_index.jsonl` 和 SQLite 状态中清理掉。脚本会和 Codex 删除 thread 的源码语义保持一致：从用户请求的 root session 出发，沿 `thread_spawn_edges.parent_thread_id -> child_thread_id` 自动展开并清理整棵 spawned subtree。代价是这些子会话不再能从 Codex App 中打开或 resume。

`thread_spawn_edges.status = closed` 只能说明 Codex 本地 spawn edge 生命周期已经关闭；它不能证明子 Agent 的结论已经完整写回父会话，也不能代表用户以后一定不需要打开子会话。因此脚本默认只允许用户显式请求 closed SubAgent root，但仍要求用户确认 dry-run 计划。root 允许后，descendants 会作为该 root subtree 的一部分一起清理，即使某个 descendant edge 状态为 open。

脚本会选择最新且实际提到 requested root 的 `state_*.sqlite` 作为 canonical cleanup source，并只用这个 DB 展开实际 cleanup tree。其它 state DB 只做诊断；如果它们看到额外 descendants，会列入 `stale_spawn_descendant_candidates`，不会静默加入 cleanup IDs。若多个 `state_*.sqlite` 对同一个显式请求 root 报告冲突状态，例如一个 DB 显示 `closed`、另一个显示 `open`，脚本会拒绝清理并在 dry-run 中列出来源。正式 `--apply` 前，脚本还会检测 Codex App / `codex` CLI 是否看起来正在使用目标 Codex home；默认拒绝，除非用户显式传 `--allow-running-codex`。在 macOS/Linux 上脚本会尽量读取进程环境中的 `CODEX_HOME`；未声明 home 的 Codex 进程按默认 `~/.codex` 处理。

## 适用条件

适合清理：

- `list_rollouts.py --show-title` 显示为 `【Sub】...` 的会话。
- dry-run 显示 `status=closed`。
- 子 Agent 的有用结论已经出现在父会话中。
- 用户不再需要该 root 及其 spawned descendants 的完整工具调用、截图和中间推理记录。

不建议清理：

- 子 Agent 仍在运行。
- dry-run 显示 `status=open` 或 `status=unknown`，除非用户明确确认并使用 `--allow-open-subagent`。
- 父会话仍需要继续等待或 resume 这个子 Agent 或其 descendants。
- 用户还想从 Codex App 中打开该 root 或 descendants 查看完整历史。
- 无法确认目标 ID 是否为 SubAgent。

## 自动化脚本

脚本位置：

```bash
python scripts/cleanup_session_by_id.py --help
```

先 dry-run：

```bash
python scripts/cleanup_session_by_id.py <session-id>
```

输出会列出：

- 请求的 root IDs；
- 自动展开的 descendant IDs；
- 最终 cleanup IDs；
- canonical state DB 与 secondary state DB 诊断信息；
- 是否识别为 SubAgent；
- spawn edge 状态，例如 `closed`、`open` 或 `unknown`；
- descendant open/unknown/conflict warning；
- secondary state DB 中只作诊断、不实际清理的 stale descendant candidates；
- 是否存在跨 state DB 的 spawn edge 状态冲突；
- 将删除的 rollout / archived rollout / `*.jsonl.zst` / `.orig` 文件；
- `session_index.jsonl` 命中行数；
- 各 SQLite 表中将处理的行数。

确认后关闭 Codex，再执行：

```bash
python scripts/cleanup_session_by_id.py <session-id> --apply --yes
```

如果用户明确确认“不用备份”或“直接清理”，可以跳过 cleanup 备份：

```bash
python scripts/cleanup_session_by_id.py <session-id> --apply --yes --no-cleanup-backup
```

多 ID 批量清理：

```bash
python scripts/cleanup_session_by_id.py <id-1> <id-2> <id-3>
python scripts/cleanup_session_by_id.py <id-1> <id-2> <id-3> --apply --yes
```

默认只允许显式请求的 root 是 closed SubAgent。如果用户确认 open/unknown root 子会话可丢：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-open-subagent --apply --yes
```

如果用户明确要在目标 Codex home 可能仍被 Codex 使用时清理：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-running-codex --apply --yes
```

通常不要这么做；更推荐关闭使用目标 Codex home 的 Codex 实例后执行。

如果用户明确要删普通 root session：

```bash
python scripts/cleanup_session_by_id.py <session-id> --allow-non-subagent --apply --yes
```

## 清理范围

文件：

- `CODEX_HOME/sessions/**/rollout-*<cleanup-id>.jsonl`
- `CODEX_HOME/archived_sessions/**/rollout-*<cleanup-id>.jsonl`
- 对应 `*.jsonl.zst`
- 默认包含 `*.jsonl.orig` / `*.jsonl.zst.orig` 这类 sidecar 备份

索引：

- `CODEX_HOME/session_index.jsonl` 中任一 cleanup id 的 `id` / `thread_id` / `threadId` / `session_id` / `sessionId` 命中记录

SQLite：

- `state_*.sqlite`
  - `threads.id`
  - `thread_dynamic_tools.thread_id`
  - `thread_spawn_edges.parent_thread_id`
  - `thread_spawn_edges.child_thread_id`
  - `agent_jobs.id`：当 job runner 和 worker 都在 cleanup IDs 中时，将相关 pending/running job 标记为 `cancelled`
  - `agent_job_items.assigned_thread_id`：清空 assignment，不删除 job 行
- `logs_2.sqlite`
  - `logs.thread_id`
- `goals_1.sqlite`
  - `thread_goals.thread_id`
- `memories_1.sqlite`
  - `stage1_outputs.thread_id`

脚本会自动识别 `CODEX_HOME`、`config.toml` 中的 `sqlite_home`、`CODEX_SQLITE_HOME`，也可以显式传：

```bash
python scripts/cleanup_session_by_id.py <session-id> \
  --codex-home ~/.codex \
  --sqlite-home ~/.codex
```

## 备份与恢复

默认执行前，脚本会把所有将删除或改写的文件复制到：

```text
<codex-home>/backups/session-cleanup-<UTC timestamp>/
```

并写出：

```text
cleanup-manifest.json
```

恢复时，关闭 Codex，先 dry-run，再根据 manifest 中的 `backups` 字段把文件复制回原路径：

```bash
python scripts/restore_cleanup_manifest.py <cleanup-manifest.json>
python scripts/restore_cleanup_manifest.py <cleanup-manifest.json> --apply --yes
```

SQLite 恢复通常应整库恢复，不要只手工插回单行。

manifest 还会记录每个被修改 SQLite DB 在清理前是否存在 `-wal` / `-shm` sidecar。如果清理失败并自动恢复，脚本会删除这次失败中新生成的 sidecar，避免恢复主库后残留不一致的 WAL/SHM。

如果传入 `--no-cleanup-backup`，脚本不会创建
`<codex-home>/backups/session-cleanup-*`，也不会生成可恢复的
`cleanup-manifest.json`。JSON 结果会显示 `cleanup_backup_enabled: false`。
这种模式不可逆，只能在用户明确表示不用备份时使用。直删模式下，脚本会先执行 SQLite 修改和 `PRAGMA integrity_check`，再重写
`session_index.jsonl` 并删除 rollout 文件；这样数据库预检或写入失败时，不会先删掉大 JSONL。

## 验证

脚本应用后会对修改过的 SQLite 数据库执行：

```sql
PRAGMA integrity_check;
```

如果返回不是 `ok`，默认备份模式会尝试从 cleanup backup 自动恢复。若自动恢复失败，应关闭 Codex 后根据 manifest 手动恢复相关 SQLite 文件。使用 `--no-cleanup-backup` 时没有自动恢复来源，脚本会直接报错并保留当时的错误信息。

合成测试覆盖：

```bash
python3 -m py_compile scripts/*.py tests/test_synthetic_rollout.py
python3 tests/test_synthetic_rollout.py
```
