# Contributing

感谢参与改进 `codex-session-compress`。这个项目处理的是用户本地 Codex 会话文件，因此所有贡献都必须优先考虑数据安全和可回滚性。

## 开发原则

- 仅使用 Python 标准库，保持可移植。
- 保持脚本可直接运行，不要求安装包。
- 处理大文件时使用流式读写，不把完整 JSONL 读入内存。
- 默认行为必须保守；删除或覆盖类操作需要明确确认。
- 新增功能必须更新 `README.md` 和 `SKILL.md`。
- 不提交真实用户会话文件。

## 本地检查

提交前运行：

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

如果增加测试数据，请使用合成 fixtures，不要使用真实 `~/.codex/sessions` 内容。

## 修改压缩逻辑时的要求

压缩策略只能是 semantic checkpoint cut：

1. 定位最新带 `replacement_history` 的 full `compacted` checkpoint。
2. 完整保留第一条 `session_meta`、该 checkpoint、以及 checkpoint 之后的所有 rollout 行。
3. 在 `GOAL_SIZE` 剩余预算内，从 checkpoint 前旧历史的两端优先保留原始 `event_msg`，并把 checkpoint 前的 `turn_context` 放在同一 UI breadcrumb 优先级中尽量保留，帮助 Codex App / `codex resume` 上翻旧聊天。
4. 如仍有预算，再从两端保留原始 `response_item` raw-detail breadcrumb。
5. 用一个显式 synthetic maintenance turn 表示被截去的旧历史中段：依次写入 `event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete`，并把它插入到 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置。synthetic `user_message.client_id` 必须使用 `codex-session-compress-elision-` 前缀，文案需说明它由压缩工具写入、不是原始用户指令，并按操作系统语言选择中文或英文。

没有 full compacted checkpoint 时必须失败，不允许添加图片替换、工具输出截断或旧 turn 裁剪作为替代路径。

真实压缩后的完成条件包括两层验证：`verify_rollout.py` 的静态 semantic checkpoint 验证，以及 `confirm_session_with_codex_cli.py` 通过 `codex app-server --stdio` 执行的只读 `thread/read includeTurns=true` 确认。静态验证必须检查 synthetic maintenance turn 是完整连续的四事件结构；含 synthetic maintenance turn 的压缩结果必须能在 Codex 重建后的同一个可见 thread turn 中看到 synthetic `userMessage` 与 synthetic `agentMessage`。

压缩备份必须在压缩阶段保留。只有在用户明确确认压缩结果可用后，才允许通过 `cleanup_compression_backups.py` 清理；该脚本默认 dry-run，真实删除必须同时传 `--apply --yes`。

## 修改清理逻辑时的要求

- 默认必须是 dry-run。
- 正式清理必须要求 `--apply --yes`。
- 默认只能清理可识别为 SubAgent 的 session；普通 session 必须要求显式 `--allow-non-subagent`。
- cleanup tree 必须来自 canonical state DB；secondary state DB 只能用于诊断 stale descendant candidates。
- `--apply` 的 running Codex guard 必须按目标 Codex home 判断，并尽量读取进程环境中的 `CODEX_HOME`；未声明 home 的 Codex 进程按默认 `~/.codex` 处理，不应把当前脚本自身的 `CODEX_HOME` 套用到所有 Codex 进程。
- 如果 job runner 和 worker 都在 cleanup IDs 中，清理逻辑必须按 Codex 源码取消相关 `agent_jobs`。
- 删除 rollout、`*.jsonl.zst`、`session_index.jsonl` 或 SQLite 行之前必须备份原文件。
- SQLite 修改后必须运行完整性检查。
- 测试只能使用合成 JSONL 和合成 SQLite，不得读取或修改真实 `~/.codex`。

## Pull Request 建议

PR 描述建议包含：

- 改动目的。
- 影响的脚本。
- 是否涉及 rollout 修改逻辑。
- 本地检查命令输出。
- 是否更新文档。

## License

提交代码即表示你同意以 MIT License 授权你的贡献。
