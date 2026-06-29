# Changelog

## 1.8.18 - Unreleased

- README 安装说明改为直接安装到 Agent 自身 skills 目录；手动安装以 Codex 为例，目标路径为 `${CODEX_HOME:-$HOME/.codex}/skills/codex-session-compress`。
- README 移除先克隆到 `~/codex-session-compress` 再软链接的安装方式，避免把用户引导到 Agent skills 目录之外。
- Agent + Prompt 自动安装说明同步为“在 skills 目录内 clone 或 git pull --ff-only”。
- README 后续重复安装段落改为“运行环境”，避免出现两套安装方式。
- 版本号同步更新到 `1.8.18`。

## 1.8.17 - Unreleased

- README 顶部调整为 GitHub 风格：居中标题、英文副标题和 version / Python / license / stdlib-only badges。
- README 移除单独的“公开仓库”链接段，安装命令中保留必要的 clone URL。
- 版本号同步更新到 `1.8.17`。

## 1.8.16 - Unreleased

- README 新增靠前的“安装”小节，提供手动软链接安装和 Agent + Prompt 自动安装两种方式，并使用准确公开仓库地址 `https://github.com/chenjh16/codex-session-compress.git`。
- README 将“普通用户快速开始”改为“快速开始”，保持首页更简洁。
- README 后续“安装说明”中的占位仓库地址替换为真实 GitHub 仓库地址。
- `pyproject.toml` 的 Homepage / Repository 更新为准确 GitHub 仓库地址。
- 版本号同步更新到 `1.8.16`。

## 1.8.15 - Unreleased

- README 首页改为面向普通 skill 用户的快速说明：先给出核心用途、常用命令示例、核心功能列表和简洁 Mermaid 图示，再进入背景与详细策略。
- README 保留“模型上下文无损压缩”作为主打能力，但把实现细节下沉到“保留策略概览”和后续章节，降低首次阅读门槛。
- 版本号同步更新到 `1.8.15`。

## 1.8.14 - Unreleased

- README 开头强化“对后续 Agent 模型上下文无损物理清理”作为主打特性，并明确无损边界：完整保留最新 full `CompactedItem.replacement_history` checkpoint 与其后的完整 `rollout_suffix`。
- README 补充其它信息的保留方式：在 `GOAL_SIZE` 预算内优先保留 Codex App / `codex resume` 可见历史所需的 `event_msg` 与 checkpoint 前 `turn_context`，再保留原始 `response_item` raw breadcrumb，并用 synthetic maintenance turn 标记被物理删除的旧历史中段。
- README 新增 Mermaid 压缩流程图，展示 full checkpoint 检测、mandatory segment 保留、GOAL_SIZE breadcrumb 分配、备份、静态验证和 Codex CLI/app-server confirmation 流程。
- 版本号同步更新到 `1.8.14`。

## 1.8.13 - Unreleased

- README 移除过期的外部项目来源说明；当前 skill 的压缩和清理方案按本项目实现与 `openai/codex` 源码语义描述，避免误导读者以为仍依赖旧项目方案。
- 许可证说明收敛为项目自身的 MIT 许可，不再保留不准确的改编归因。
- 版本号同步更新到 `1.8.13`。

## 1.8.12 - Unreleased

- `confirm_session_with_codex_cli.py --require-synthetic-marker` 不再只要求可见 history 中分别存在 synthetic user/agent 消息，而是要求二者出现在同一个 reconstructed thread turn 中，和当前 synthetic maintenance turn 方案一致。
- confirmation JSON 新增 `synthetic_turn_indexes`，终端输出显示 `same_turn`，失败信息也明确指向 user+agent pair 未出现在同一 turn。
- `list_rollouts.py` 与 `verify_rollout.py` 的文件头说明同步当前实现：默认 sessions base 尊重 `CODEX_HOME`，verifier 会检查 timestamp、synthetic marker 与 semantic active suffix call pairing。
- README、SKILL、AGENTS、CONTRIBUTING、策略文档和 troubleshooting 统一为当前唯一的 semantic checkpoint cut + synthetic maintenance turn 方案，删除“只要 synthetic 消息分别可见即可”的旧验收表述，并修正 cleanup backup 默认路径为 `<codex-home>/backups/...`。
- 合成测试新增 confirmation 同一 turn 语义覆盖。
- 版本号同步更新到 `1.8.12`。

## 1.8.11 - Unreleased

- `confirm_session_with_codex_cli.py` 去掉 `select.select` 读取 stdout 的实现，改为后台线程读取 stdout/stderr，提升 Windows/非 POSIX 平台兼容性，并避免 stderr 管道积压导致 app-server 阻塞。
- `cleanup_session_by_id.py` 与 `restore_cleanup_manifest.py` 的 running Codex guard 会尽量读取进程环境中的 `CODEX_HOME`；未声明 home 的 Codex 进程按默认 `~/.codex` 处理，目标 home 判断更明确。
- `restore_cleanup_manifest.py` 的 running guard 现在使用 manifest 中的 `plan.codex_home` 作为目标 home，不再全局拦截所有 Codex 进程。
- `list_rollouts.py --show-title` 在显式传入 `--base .../sessions` 或 `--base .../archived_sessions` 时，优先使用该 base 的父目录作为标题元数据 Codex home，再回退到环境变量。
- SQLite URI 构造统一使用 `Path.as_posix()` 并保留 drive colon，改善 Windows 路径兼容性。
- cleanup 备份路径构造会把 POSIX 根目录或 Windows drive/anchor 转成安全目录段，避免 Windows 绝对路径在 `<backup>/files/...` 下拼接时逃出备份目录。
- `repair_rollout.py --auto --json` 不再输出人类可读选择 banner，保持 stdout 为纯 JSON。
- 合成测试新增显式 base 覆盖环境 CODEX_HOME、`repair --auto --json` 纯 JSON、队列式 app-server 响应读取、进程环境 CODEX_HOME 过滤和 Windows 风格 SQLite URI 覆盖。
- 版本号同步更新到 `1.8.11`。

## 1.8.10 - Unreleased

- `compress_session_by_id.py` 新增 `--codex-home`，并在 `--base` 指向非默认 `sessions` / `archived_sessions` 时推断相同 Codex home 给 `confirm_session_with_codex_cli.py` 和可选 `codex resume` 使用。
- `repair_rollout.py` 的 placeholder 统计改为“物理省略的 checkpoint 前 rollout 行数”，不再只统计 breadcrumb 候选；即使省略的是旧 `compacted` / `world_state` 等非 breadcrumb 行，也会注入 synthetic maintenance turn。
- `repair_rollout.py --json` 新增 `historical_rollout_lines_candidates` / `historical_rollout_lines_omitted`，同时保留 breadcrumb 候选维度的省略统计。
- `verify_rollout.py --semantic-checkpoint` 新增 synthetic maintenance turn 结构校验：必须是连续完整的 `task_started -> user_message -> agent_message -> task_complete`，且 ID / marker 对齐，并且仍必须位于最新 full checkpoint 之前。
- `cleanup_session_by_id.py --apply` 的 running Codex guard 改为按目标 Codex home 过滤，减少其它独立 `CODEX_HOME` 实例造成的误拦截。
- 文档统一把 `turn_context` 描述为 checkpoint 前同一 UI-breadcrumb 优先级内保留，不再使用严格“相邻”表述。
- 合成测试新增多 Codex home confirmation 推断、物理省略 placeholder、畸形 synthetic turn 拒绝和目标 home 进程过滤覆盖。
- 版本号同步更新到 `1.8.10`。

## 1.8.9 - Unreleased

- 新增 `cleanup_compression_backups.py`，用于清理已确认可用的压缩备份；默认 dry-run，真实删除必须同时传 `--apply --yes`。
- `cleanup_compression_backups.py` 仅匹配 sessions 目录中的 `rollout-*.jsonl.orig` / `rollout-*.jsonl.orig.N`，不触碰 active rollout、SQLite state 或 SubAgent cleanup manifest。
- `compress_session_by_id.py` 的压缩完成提示改为指向备份清理脚本，当前流程统一使用 dry-run / `--apply --yes` 清理压缩备份。
- README、SKILL、AGENTS、CONTRIBUTING、SECURITY 和 troubleshooting 文档统一更新为：压缩阶段保留备份，用户确认后使用 cleanup 脚本 dry-run/apply 清理。
- 合成测试新增压缩备份清理覆盖，验证 `--apply` 必须搭配 `--yes`，并且只删除目标 session 的备份。
- 版本号同步更新到 `1.8.9`。

## 1.8.8 - Unreleased

- 新增 `confirm_session_with_codex_cli.py`：通过 `codex app-server --stdio` 发送只读 `thread/read includeTurns=true`，确认压缩后 session 能被 Codex 自己重建为 thread history。
- `compress_session_by_id.py` 在真实压缩并通过 `verify_rollout.py` 后，默认运行 Codex CLI confirmation；如果 rollout 含 synthetic maintenance turn，会要求看到 synthetic `userMessage` 和 synthetic `agentMessage`。（当前 `1.8.12` 起进一步要求二者位于同一个 reconstructed thread turn。）
- 新增 `--skip-codex-cli-confirm` 与 `--codex-cli-confirm-timeout`；跳过默认 CLI confirmation 只应在用户明确要求时使用。
- Codex CLI confirmation 失败时默认和 verifier 失败一样，将 `.orig` 备份 rename 回原路径恢复。
- `verify_rollout.py --semantic-checkpoint` 对 active suffix 中普通 `function_call` / `function_call_output` 的 `call_id` 不匹配改为报告但不阻断；Codex 源码的 `normalize_history` 会在模型输入前插入缺失 output 或移除孤儿 output。
- 文档同步说明：默认压缩完成条件现在包括静态 JSONL verifier 和 Codex CLI/app-server 可见历史确认。
- 版本号同步更新到 `1.8.8`。

## 1.8.7 - Unreleased

- `repair_rollout.py` 的 in-place 压缩备份从 `shutil.copy2` 改为同一文件系统内 rename：先写完临时瘦身 JSONL，再把原 rollout rename 为 `.orig` / `.orig.N`，最后安装瘦身文件。
- `--backup-dir` 现在必须和 rollout 位于同一文件系统；如果不是，脚本会拒绝执行，避免静默复制多 GB 原文件。
- `compress_session_by_id.py` 在验证失败时会把备份 rename 回原路径恢复，避免失败恢复路径再次复制大文件。
- 文档同步说明新的峰值磁盘占用模型：正常压缩约为“原文件备份 + 新压缩文件”，不再额外产生完整原文件拷贝。
- 合成测试新增 inode 检查，确认 `.orig` 备份来自 rename 而不是 copy。
- 版本号同步更新到 `1.8.7`。

## 1.8.6 - Unreleased

- `repair_rollout.py` 的 elision placeholder 改为 checkpoint 前的 synthetic maintenance turn：`event_msg.task_started`、`event_msg.user_message`、`event_msg.agent_message`、`event_msg.task_complete`。
- placeholder 现在插入到 checkpoint 前旧历史两端 breadcrumb 中间的真实截断位置；synthetic `user_message` 不进入 checkpoint 后 active suffix。
- 注入文案会按操作系统语言选择：macOS 优先读取 `AppleLocale`，再回退 `LC_ALL` / `LC_MESSAGES` / `LANGUAGE` / `LANG`；中文系统写中文，其它语言默认英文。
- `verify_rollout.py --semantic-checkpoint` 新增检查：带 `codex-session-compress-elision-` 前缀的 synthetic marker 必须位于最新 full checkpoint 之前。
- JSON 输出更新为 `placeholder_type=event_msg.synthetic_maintenance_turn.user_message_agent_message`、`placeholder_line_count=4`、`placeholder_position=pre-checkpoint-elision-gap`，并报告 `placeholder_language`。
- 合成测试覆盖 checkpoint 后 synthetic user marker 的拒绝场景。
- 版本号同步更新到 `1.8.6`。

## 1.8.5 - Unreleased

- 该版本的 placeholder 可见性修复已被 `1.8.6` 的 synthetic maintenance turn 规范吸收。
- 历史实现细节已从文档中移除；当前唯一规范见 `1.8.6`。

## 1.8.4 - Unreleased

- 该版本的 placeholder 位置实验已被 `1.8.6` 废弃。
- 历史实现细节已从文档中移除；当前唯一规范见 `1.8.6`。

## 1.8.3 - Unreleased

- 该版本的 placeholder 文档收敛已被 `1.8.6` 替代。
- 当前唯一规范见 `1.8.6`。

## 1.8.2 - Unreleased

- 该版本的 placeholder 显式 turn 实验已被 `1.8.6` 替代。
- 当前唯一规范见 `1.8.6`。

## 1.8.0 - Unreleased

- `repair_rollout.py` 的 optional historical breadcrumb 策略改为优先保留 checkpoint 前两端的原始 `event_msg` / `turn_context`，让 Codex App / `codex resume` 在 `GOAL_SIZE` 允许时可以继续上翻旧聊天。
- 旧 `response_item` 仍会在 UI breadcrumb 后用剩余预算从两端保留，用于保存 raw model-detail breadcrumb；mandatory segment 仍是第一条 `session_meta`、最新 full `CompactedItem.replacement_history` checkpoint 和之后的完整 suffix。
- elision placeholder 作为 App-visible historical event marker 进入压缩策略；当前唯一 placeholder 规范见 `1.8.6`。
- `verify_rollout.py --allow-active-images` 的历史图片白名单扩展为预算内保留的 historical breadcrumb，包括 `event_msg`、`turn_context` 和 `response_item`。
- JSON 结果新增 historical UI breadcrumb 与 raw response breadcrumb 的分项计数。
- 合成测试新增 App-visible old `event_msg` / `turn_context` breadcrumb 保留覆盖。
- README、SKILL、docs、references 同步说明 App 上翻目标和新优先级。

## 1.7.1 - Unreleased

- `cleanup_session_by_id.py` 新增 `--no-cleanup-backup`，在用户明确要求“不用备份/直接清理”时跳过 `<codex-home>/backups/session-cleanup-*` 备份创建，直接清理 rollout、`session_index.jsonl` 和 SQLite 状态。
- 直删模式下，脚本会先执行 SQLite 修改与 `PRAGMA integrity_check`，再重写 `session_index.jsonl` 并删除 rollout 文件；JSON 结果会标记 `cleanup_backup_enabled: false`，且不可用 `restore_cleanup_manifest.py` 回滚。
- 文档和 skill safety rules 更新为：压缩/修复 rollout 仍必须备份；SubAgent 清理默认备份，但用户明确确认后可使用不可逆直删。

## 1.7.0 - Unreleased

- `cleanup_session_by_id.py` 对齐 Codex `delete_threads_strict` 的 agent job 语义：当 job runner 和 worker 都在 cleanup subtree 中时，相关 pending/running `agent_jobs` 会标记为 `cancelled`，同时继续清空匹配 `agent_job_items.assigned_thread_id`。
- cleanup tree 现在只使用最新且实际提到 requested root 的 canonical `state_*.sqlite` 展开；其它 state DB 仅用于诊断，额外 descendants 会进入 `stale_spawn_descendant_candidates`，不会静默加入 cleanup IDs。
- dry-run 文本和 JSON 会更清楚显示 canonical/secondary state DB、descendant open/unknown/conflict warning，以及 stale descendant candidates。
- `verify_rollout.py --allow-active-images` 现在允许 latest full compacted checkpoint 本行 `replacement_history` 中的图片，并修正 `input_image` data URI 双计数。
- `repair_rollout.py`、`verify_rollout.py`、`compress_session_by_id.py` 新增 `--json` 输出，便于新 Agent 批量处理。
- `compress_session_by_id.py` 新增 `--verify-only` 与 `--force-semantic-verify`。
- 新增 `restore_cleanup_manifest.py`，可根据 `cleanup-manifest.json` dry-run 或显式恢复已应用的 cleanup 备份。
- 合成测试新增 agent job cancel、canonical/secondary state DB、checkpoint 图片、JSON 输出、verify-only、cleanup manifest restore 覆盖。

## 1.6.3 - Unreleased

- `cleanup_session_by_id.py --apply` 默认检测 Codex App / `codex` CLI 进程；发现仍在运行时拒绝执行，除非显式传 `--allow-running-codex`。
- cleanup manifest 现在记录 SQLite `-wal` / `-shm` sidecar 的清理前存在状态；如果清理失败并恢复备份，会删除本次失败尝试中新生成的 sidecar，避免主库恢复后残留新 WAL/SHM。
- dry-run 现在检测多个 `state_*.sqlite` 对显式请求 root session 报告的 spawn edge 状态冲突；例如同时看到 `closed` 和 `open` 会进入 `refused_status_conflict` 并拒绝清理。
- 合成测试新增 running Codex guard、失败恢复 sidecar 删除、跨 state DB 状态冲突覆盖。

## 1.6.2 - Unreleased

- `cleanup_session_by_id.py` 现在会按 Codex 源码的 thread deletion 语义，从请求的 root session ID 出发，沿 SQLite `thread_spawn_edges` 自动展开并清理 spawned descendants。
- dry-run 计划新增 `requested_session_ids`、`descendant_session_ids`、`spawn_tree_edges`，并在文本输出中区分 Requested、Descendants 和 Cleanup IDs。
- 安全闸仍然只对用户显式请求的 root 执行：root 必须是 closed SubAgent，open/unknown root 需要 `--allow-open-subagent`，普通 root 需要 `--allow-non-subagent`；descendants 会作为 root subtree 的一部分清理。
- 合成测试新增 root SubAgent -> child -> open grandchild 场景，验证 descendants 的 rollout、session_index 和 SQLite 状态会随 root 一起清理，同时单独请求 open SubAgent 仍默认拒绝。

## 1.6.1 - Unreleased

- 清理脚本默认条件收紧为 closed SubAgent：必须能识别为 SubAgent，且 SQLite `thread_spawn_edges.status` 为 `closed`；open/unknown 子会话需要显式 `--allow-open-subagent`。
- `cleanup_session_by_id.py` 的 `agent_job_items` 处理改为清空 `assigned_thread_id` 并标记 pending/error，而不是删除 job 行，更贴近 Codex 源码里的 delete thread 语义。
- 清理执行前会先抢 SQLite 写锁；修改中若失败或 `PRAGMA integrity_check` 非 `ok`，会尝试从 cleanup backup 自动恢复。
- `verify_rollout.py --semantic-checkpoint` 现在按 `call_id` 验证 active suffix 的 `function_call` / `function_call_output` 配对，并将图片放行收窄到 active suffix 或历史 `ResponseItem` breadcrumb。
- `list_rollouts.py --show-title` 在 auto 模式下会让 SubAgent 的 SQLite `agent_nickname` 覆盖 session_index 的长 prompt 标题。
- `compress_session_by_id.py` 新增 `--include-archived`，可同时搜索 sessions sibling `archived_sessions`。
- 文档明确 breadcrumb 是从 latest full compacted 前旧历史两端交替保留，预算满后截去中间超额内容。
- `.gitignore` 增加 `.jsonl.zst`、SQLite、cleanup backup/manifest 等本地运行产物。

## 1.6.0 - Unreleased

- 新增 `cleanup_session_by_id.py`，用于按 session ID dry-run 并清理已完成 SubAgent 的本地残留。
- 清理脚本默认只允许 SubAgent，会从 rollout `session_meta` 与 SQLite `threads` 元数据识别 `thread_source` / `source.subagent` / `agent_nickname`。
- 清理范围覆盖 active/archived rollout、`*.jsonl.zst` 压缩 sibling、可选 `.jsonl.orig` 备份、`session_index.jsonl` 名称记录，以及 SQLite 中的 `threads`、`thread_dynamic_tools`、`thread_spawn_edges`、`agent_job_items`、`logs`、`thread_goals`、`stage1_outputs` 记录或引用。
- 正式清理必须同时传 `--apply --yes`；应用前会备份所有将删除或改写的文件，并写出 `cleanup-manifest.json`。
- 合成测试新增 SubAgent 清理覆盖，包括 dry-run、拒绝普通 session、备份 manifest、SQLite 行删除和 `PRAGMA integrity_check`。

## 1.5.2 - Unreleased

- `list_rollouts.py` 默认表格和 JSON 输出新增 `project` / `project_dir_name`，取自 `session_meta.cwd` 的最后一级目录名。
- sub-agent 会话标题现在统一加 `【Sub】` 前缀，例如 `【Sub】Kepler`，便于和普通会话区分。
- 合成测试覆盖默认 project 输出和 sub-agent 标题标签。

## 1.5.1 - Unreleased

- `list_rollouts.py --show-title` 的 SQLite 兜底现在会识别 sub-agent 会话，并优先使用 `threads.agent_nickname` 作为 Codex App 短标题。
- 该行为已用官方 app-server `thread/list useStateDbOnly=true` 只读验证：sub-agent 返回 `agentNickname`，而 `threads.title` 可能只是长 first user prompt。
- 合成测试新增 sub-agent `agent_nickname` 标题覆盖，防止回退到显示长 prompt。
- `SKILL.md` / `README.md` 同步说明新的标题来源优先级。

## 1.5.0 - Unreleased

- 移除多策略压缩入口，压缩入口只保留 semantic checkpoint cut。
- 移除旧修复代码路径，不再支持图片改写、输出截断或按旧轮次裁剪。
- 移除旧兼容参数，统一使用 `--goal-size SIZE`。
- 没有 full compacted checkpoint（`payload.replacement_history` 为数组）时，压缩脚本会失败并拒绝修改文件。
- 明确 `GOAL_SIZE` 内应尽量保留原始 `ResponseItem` 历史 breadcrumb，包括旧工具输出和旧图片；这些 breadcrumb 不参与 active resume reconstruction。
- `repair_rollout.py` 在结果仍超过 V8 单字符串上限时改为硬失败，不再继续写出或替换文件。
- `repair_rollout.py` 写回压缩结果时保留原 rollout 文件权限，并拒绝重写非 UTF-8 rollout。
- `verify_rollout.py --semantic-checkpoint` 现在隐式要求 compacted/full compacted checkpoint，并报告 retained historical breadcrumb 图片。
- `compress_session_by_id.py` 不再为了设置验证参数而预先完整扫描原始大 rollout。
- `list_rollouts.py` 的 SQLite 标题兜底现在会跨多个 `state_*.sqlite` 合并缺失标题。
- 合成测试新增 semantic checkpoint 强制 full checkpoint、预算内保留原始 response item、历史图片放行、跨 SQLite 标题合并覆盖。
- 全面同步 README、SKILL、AGENTS、CONTRIBUTING、references 和 docs，删除多策略与旧修复路径说明。

## 1.4.0 - Unreleased

- 新增 semantic checkpoint cut 压缩策略：保留第一条 `session_meta`、最新带 `replacement_history` 的 full `compacted`、以及 checkpoint 后所有 rollout 行。
- 新增 `--goal-size SIZE`，默认 `100MB`，支持 `80MB`、`1GB`、`1.5GiB` 等自然大小写法。
- `repair_rollout.py` 引入 semantic checkpoint cut 的早期版本。
- `repair_rollout.py` 新增 `--output PATH`，可生成压缩副本而不替换原文件。
- `verify_rollout.py` 新增 `--require-full-compacted`、`--semantic-checkpoint`、`--allow-active-images`，可验证 active suffix 的函数调用配对并允许 checkpoint 后图片保留。
- `compress_session_by_id.py` 默认使用新的 goal-size 参数，并在原始 rollout 含 full checkpoint 时自动启用 semantic verification。
- 新增 `docs/semantic-checkpoint-compression-strategy.zh.md`，并重写 Codex 上下文压缩源码分析文档。
- 合成测试更新为覆盖 semantic checkpoint cut、active suffix 图片保留和 full checkpoint 验证。

## 1.3.0 - Unreleased

- `list_rollouts.py` 新增 `--show-title`，可从本地只读 Codex 元数据补充 Codex App 风格标题。
- 标题来源默认 `auto`：优先读取 `session_index.jsonl.thread_name` 的最后一条匹配记录，缺失时只读查询 `state_*.sqlite` 的 `threads.title`。
- `list_rollouts.py` 新增 `--title-source`、`--codex-home`、`--sqlite-home`，便于显式控制标题来源与状态目录。
- 合成测试覆盖 session index 最新标题优先和 SQLite 标题兜底。

## 1.2.2 - Unreleased

- `repair_rollout.py --auto` 现在默认尊重 `CODEX_HOME/sessions`，未设置 `CODEX_HOME` 时回退到 `~/.codex/sessions`。
- `repair_rollout.py --auto` 新增 `--base PATH`，可显式指定扫描目录。
- 更新 README 与 SKILL 文档，说明 `repair_rollout.py --auto` 的路径行为。

## 1.2.1 - Unreleased

- 修复旧轮次裁剪可能移除最新 `compacted` 检查点的问题。
- `verify_rollout.py` 现在会报告 compacted 数量，并支持 `--require-compacted`。
- `compress_session_by_id.py` 会在原始 rollout 含有 compacted 时要求修复后仍保留 compacted。
- `list_rollouts.py` 与 `compress_session_by_id.py` 默认尊重 `CODEX_HOME`。
- 更新合成测试，覆盖最新 compacted 保留和 require-compacted 验证。

## 1.2.0

- 新增 `AGENTS.md`，明确 agent 工作规范和安全规则。
- 新增标准 GitHub 项目文件：`README.md`、`LICENSE`、`SECURITY.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、`pyproject.toml`。
- 新增 `references/rollout-format.md` 和 `references/troubleshooting.md`。
- 增强 `list_rollouts.py`：支持 `--base`、`--show-cwd`、`--json`、`--all-jsonl`。
- 增强 `compress_session_by_id.py`：支持 `--scan-meta`、`--list-backups`、验证失败自动恢复。
- 保持 `repair_rollout.py` 与 `verify_rollout.py` 的最小损失修复流程。

## 1.1.0

- 新增 `list_rollouts.py`，支持按大小列出前 N 个 Codex rollout JSONL。
- 更新 `SKILL.md`，加入发现大文件和按 ID 压缩的工作流。

## 1.0.0

- 初始版本：按 session ID 定位 rollout 并调用 repair/verify 脚本进行压缩。
