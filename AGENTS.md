# AGENTS.md

本项目是一个可移植的 Codex/Claude Code/OpenCode 等 agent skill，用于管理本地 Codex 会话 JSONL rollout 文件。

## 项目目标

- 帮助用户列出 `CODEX_HOME/sessions` 或 `~/.codex/sessions` 下体积最大的 Codex rollout JSONL 文件。
- 让用户根据 session ID 选择需要压缩的会话。
- 在压缩前始终备份原始 JSONL。
- 使用语义优先策略压缩会话，优先保留 Codex resume reconstruction 需要的 full compacted checkpoint 与 active suffix。
- 压缩后运行本地验证脚本，并默认通过 Codex CLI/app-server 的只读 `thread/read includeTurns=true` 确认可见历史和 synthetic 压缩消息已在同一个 reconstructed turn 中成功重建；交互式 `codex resume` 只作为额外人工检查。
- 只有在用户明确确认后，才允许用 `cleanup_compression_backups.py --apply --yes` 删除压缩备份。
- 为已完成且不再需要打开的 closed SubAgent 提供按 root session ID 的本地清理能力，并按 canonical `thread_spawn_edges` 自动清理 spawned descendants；必须先 dry-run、再显式确认执行。

## 工作原则

1. **安全优先。** 不允许无备份修改 rollout，不允许自动删除 `.orig` 备份；压缩备份清理必须先 dry-run，正式删除必须使用 `cleanup_compression_backups.py --apply --yes`。
2. **语义唯一。** 只允许使用 semantic checkpoint cut：保留第一条 `session_meta`、最新 full `CompactedItem.replacement_history` checkpoint 和其后的所有 rollout lines，再按 `GOAL_SIZE` 预算从 checkpoint 前旧历史两端优先保留原始 `event_msg`，并把 checkpoint 前的 `turn_context` 放在同一 UI breadcrumb 优先级中尽量保留，剩余预算再保留原始 `response_item` raw-detail breadcrumb，中间超额内容直接截去并用显式 synthetic maintenance turn 标记。synthetic maintenance turn 必须由四条带顶层 `timestamp` 的合法 `RolloutLine` 组成：`event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete`，并插入到 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置。synthetic `user_message.client_id` 必须使用 `codex-session-compress-elision-` 前缀，并在文案中明确说明它由压缩工具写入、不是原始用户指令；文案应按操作系统语言选择，中文系统写中文，其它语言默认英文。验证器必须拒绝任何位于最新 full checkpoint 之后的 synthetic compression marker，并要求 synthetic maintenance turn 是完整连续的四事件结构，避免 synthetic user_message 进入 active suffix。没有 full compacted checkpoint 时必须失败，不能改用任何非 checkpoint 的改写路径。
3. **可回滚。** 所有破坏性操作都要保留明确的恢复命令；静态验证或 Codex CLI confirmation 失败时应默认恢复 `.orig` 备份。
4. **可移植。** 脚本必须只依赖 Python 标准库，默认可在 macOS/Linux 上运行。
5. **不要提交用户会话数据。** `rollout-*.jsonl`、`.orig`、日志、截图和任何真实用户会话数据都不能进入仓库。
6. **SubAgent 清理必须保守但完整。** 默认只允许显式请求的 root session 是可识别 SubAgent 且 `thread_spawn_edges.status = closed`；一旦 root 允许清理，必须按 canonical `thread_spawn_edges` 自动展开并清理 spawned descendants，和 Codex thread deletion 语义保持一致。正式清理必须先展示 dry-run 计划，并要求 `--apply --yes`。open/unknown root 子会话只有在用户明确要求时才能配合 `--allow-open-subagent` 清理；普通 root session 只有在用户明确要求时才能配合 `--allow-non-subagent` 清理。
7. **清理前状态必须清楚。** cleanup tree 只来自最新且实际提到 requested root 的 canonical `state_*.sqlite`；secondary state DB 只用于诊断，额外 descendants 只能列为 stale candidates。多个 `state_*.sqlite` 对显式请求 root 的 spawn edge 状态冲突时必须拒绝；`--apply` 时检测到 Codex App 或 `codex` CLI 可能正在使用目标 Codex home 时必须拒绝，除非用户明确要求 `--allow-running-codex`。running guard 应尽量读取进程环境中的 `CODEX_HOME`；未声明 home 的 Codex 进程按默认 `~/.codex` 处理，避免把当前 Agent 自己的 `CODEX_HOME` 错当成所有 Codex 进程的 home。
8. **SQLite 恢复要处理 sidecar。** cleanup 失败后恢复备份时，必须删除本次失败中新生成的 `*.sqlite-wal` / `*.sqlite-shm`，避免恢复主库后残留新 sidecar。
9. **Agent job 语义对齐源码。** 清理 root + descendants 时，如果 job runner 和 worker 都在 cleanup IDs 中，必须将相关 pending/running `agent_jobs` 标记为 `cancelled`；`agent_job_items.assigned_thread_id` 只清空，不删除 job item 行。

## 代码规范

- Python 目标版本：3.8+。
- 不引入第三方运行时依赖。
- 脚本应支持 `--help`，错误信息写到 stderr，返回合理退出码。
- 修改 rollout 时必须采用流式读写，避免把大 JSONL 文件整体读入内存。
- 处理 JSONL 时逐行解析；遇到坏行应统计并报告，除非当前操作必须中止。
- 删除或改写文件、SQLite 数据库前必须先备份原文件；SQLite 修改后应运行 `PRAGMA integrity_check`。
- 所有路径都要支持 `~` 展开。
- 文档和命令示例尽量使用中文说明，但脚本 CLI 输出可以保持英文，方便跨环境使用。

## 目录约定

```text
.
├── AGENTS.md
├── SKILL.md
├── README.md
├── LICENSE
├── SECURITY.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── pyproject.toml
├── references/
│   ├── rollout-format.md
│   └── troubleshooting.md
└── scripts/
    ├── list_rollouts.py
    ├── compress_session_by_id.py
    ├── cleanup_compression_backups.py
    ├── cleanup_session_by_id.py
    ├── restore_cleanup_manifest.py
    ├── repair_rollout.py
    ├── verify_rollout.py
    └── confirm_session_with_codex_cli.py
```

## 测试与检查

改动后至少运行：

```bash
python3 -m py_compile scripts/*.py
python3 scripts/list_rollouts.py --help
python3 scripts/compress_session_by_id.py --help
python3 scripts/cleanup_compression_backups.py --help
python3 scripts/cleanup_session_by_id.py --help
python3 scripts/restore_cleanup_manifest.py --help
python3 scripts/repair_rollout.py --help
python3 scripts/verify_rollout.py --help
python3 scripts/confirm_session_with_codex_cli.py --help
python3 tests/test_synthetic_rollout.py
```

如果新增临时 fixtures，只能放在 `tests/fixtures/`，且必须是合成数据，不能包含真实用户会话。
