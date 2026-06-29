---
name: codex-session-compress
description: >-
  List the largest local Codex session JSONL rollout files and compress a
  selected session by UUID. Use when the user wants to inspect Codex JSONL file
  sizes under CODEX_HOME/sessions or ~/.codex/sessions, sort them from largest
  to smallest, choose one or more sessions for cleanup, slim an oversized Codex
  session while keeping a reversible backup, or remove finished sub-agent
  session artifacts by session ID. Provides list_rollouts.py for top-N size
  reports with optional read-only Codex App-style title lookup,
  compress_session_by_id.py for ID-driven semantic checkpoint compression,
  cleanup_compression_backups.py for explicit post-confirmation
  compression-backup cleanup, and cleanup_session_by_id.py for dry-run first
  sub-agent cleanup with optional backups. Compression backups are never removed
  during compression; backup cleanup requires explicit user confirmation plus
  --apply --yes.
license: MIT
metadata:
  version: 1.8.22
---

# Codex Session Compress

This skill manages oversized local Codex session rollout JSONL files.

It supports five workflows:

1. List local Codex JSONL files by size, largest first.
2. Compress a chosen Codex session by its session ID while preserving a backup.
3. Remove confirmed-good compression backups after verification and user confirmation.
4. Remove finished sub-agent session artifacts by session ID with a dry-run plan
   and optional cleanup backup.
5. Restore an applied cleanup from its `cleanup-manifest.json`.

Codex stores local sessions as JSONL rollout files under:

```text
~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl
```

The `<session-id>` in the filename should match the `id` in the first
`session_meta` record. This skill uses that ID as the main handle so users do
not need to manually navigate date-based directories.

## Tools

```text
scripts/
├── list_rollouts.py
├── compress_session_by_id.py
├── cleanup_session_by_id.py
├── cleanup_compression_backups.py
├── restore_cleanup_manifest.py
├── repair_rollout.py
├── verify_rollout.py
└── confirm_session_with_codex_cli.py
```

`repair_rollout.py`, `verify_rollout.py`, and
`confirm_session_with_codex_cli.py` implement the core semantic checkpoint
compression, static verification, and Codex CLI thread-history confirmation
flow. The additional wrappers add top-N listing, ID-based compression,
confirmed compression-backup cleanup, closed/timed-out sub-agent cleanup, and
cleanup restore workflows.

## List largest Codex JSONL sessions

Use `list_rollouts.py` before cleanup so the user can choose which sessions to
compress.

```bash
# Show the 10 largest rollout JSONLs, default behavior
python scripts/list_rollouts.py

# Show the top N largest rollout JSONLs
python scripts/list_rollouts.py --top 25

# Show all matching rollout JSONLs
python scripts/list_rollouts.py --top 0

# Include cwd metadata when available
python scripts/list_rollouts.py --show-cwd

# Include Codex App-style title from local read-only metadata
python scripts/list_rollouts.py --show-title

# Include every *.jsonl under the base directory, not just rollout-*.jsonl
python scripts/list_rollouts.py --all-jsonl

# Machine-readable output
python scripts/list_rollouts.py --json
```

The output includes:

```text
rank  size  modified time  project folder  session id  path
```

With `--show-title`, the script enriches each row from local read-only Codex
metadata without scanning large rollout bodies. It uses
`session_index.jsonl.thread_name` first because that matches renamed App
sidebar titles more closely. For sub-agent sessions, SQLite
`state_*.sqlite.threads.agent_nickname` is preferred when available because
Codex App's `thread/list` returns that as the short display label and
`session_index` may contain a long first prompt. Other sessions fall back to
`state_*.sqlite.threads.title`. SQLite is opened read-only.

Sub-agent titles are displayed with a `【Sub】` prefix, for example
`【Sub】Kepler`. The default table and JSON output include the project directory
name from `session_meta.cwd` as `project`; use `--show-cwd` when the full cwd is
needed.

Use the displayed session ID with `compress_session_by_id.py`.

## Clean finished sub-agent sessions by ID

Sub-agent sessions can be cleaned when their useful result has already been
reported back to the parent thread and the user no longer needs to open the
child thread itself. Cleaning removes the child session history and UI metadata;
it does not preserve the child as a resumable thread.

Always preview first:

```bash
python scripts/cleanup_session_by_id.py <session-id>
```

Apply only after the user confirms the dry-run plan, ideally with Codex closed:

```bash
python scripts/cleanup_session_by_id.py <session-id> --apply --yes
```

If the user explicitly says no backup / direct cleanup, skip the cleanup backup:

```bash
python scripts/cleanup_session_by_id.py <session-id> --apply --yes --no-cleanup-backup
```

By default the script allows only sessions identified as sub-agents whose
SQLite `thread_spawn_edges.status` is `closed`. This is the strongest local
lifecycle signal available in Codex state; it does not prove the child result
has semantically returned to the parent, so the user must still confirm the
dry-run plan. Use `--allow-open-subagent` only after confirming an open or
unknown child is disposable. Use `--allow-timeout-subagent --timeout-hours 12`
for open/unknown sub-agents whose parent thread is at least 12 hours newer than
the child according to canonical SQLite thread activity; these are called
timed-out SubAgents. Use `--allow-status-conflict` only after confirming
conflicting spawn statuses come from stale secondary state DB metadata and the
requested sub-agent is still disposable. Use `--allow-non-subagent` only when
the user explicitly wants to delete a normal thread.

The cleanup plan covers:

- the requested session ID and every spawned descendant discovered through
  SQLite `thread_spawn_edges`;
- active and archived rollout files matching those cleanup IDs;
- compressed rollout siblings such as `*.jsonl.zst`;
- rollout sidecar backups such as `*.jsonl.orig` unless
  `--no-rollout-backups` is set;
- `session_index.jsonl` name entries for the session;
- SQLite rows or references in `threads`, `thread_dynamic_tools`,
  `thread_spawn_edges`, `agent_jobs`, `agent_job_items`, `logs`,
  `thread_goals`, and `stage1_outputs` when those tables exist locally. When
  both an agent job runner and worker are in the cleanup subtree, matching
  pending/running `agent_jobs` are marked `cancelled`; matching
  `agent_job_items.assigned_thread_id` is cleared rather than deleting the job
  row, matching Codex's local delete semantics.

By default, before applying, the script copies every file and SQLite database it will touch
to:

```text
<codex-home>/backups/session-cleanup-<UTC timestamp>/
```

It also writes `cleanup-manifest.json` and runs SQLite `PRAGMA integrity_check`
after mutation. If mutation fails, it restores backed-up files and removes any
new SQLite `-wal` / `-shm` sidecars created during the failed attempt. The
canonical cleanup tree comes from the newest `state_*.sqlite` that actually
mentions the requested root ID; other state DBs are used for diagnostics only,
and extra descendants seen only there are reported as stale candidates rather
than silently added to cleanup IDs. It also refuses ambiguous roots when
multiple state DBs report conflicting spawn edge statuses. To inspect the
machine-readable plan or result:

```bash
python scripts/cleanup_session_by_id.py <session-id> --json
python scripts/cleanup_session_by_id.py <session-id> --apply --yes --json
```

With `--no-cleanup-backup`, the script does not create
`<codex-home>/backups/session-cleanup-*` and the JSON result has
`cleanup_backup_enabled: false`. This mode is not restorable by
`restore_cleanup_manifest.py`; use it only when the user explicitly accepts
irreversible deletion. In direct cleanup mode SQLite updates and integrity
checks run before rollout file deletion, so a preflight or database failure does
not first remove large JSONL files.

`--apply` refuses to run while Codex App or `codex` CLI processes appear to be
using the target Codex home. Close that Codex instance first, or pass
`--allow-running-codex` only after explicitly accepting the risk. On
macOS/Linux the guard also tries to read each Codex process environment for
`CODEX_HOME`; a Codex process without an explicit home is treated as using the
default `~/.codex` home.

To restore a successful cleanup from its manifest, dry-run first:

```bash
python scripts/restore_cleanup_manifest.py <cleanup-manifest.json>
python scripts/restore_cleanup_manifest.py <cleanup-manifest.json> --apply --yes
```

## Compress one session by ID

```bash
# Preview the repair without modifying files
python scripts/compress_session_by_id.py <session-id> --dry-run

# Compress the selected session, default goal size 100 MB
python scripts/compress_session_by_id.py <session-id>

# Use a natural size target if the session should be smaller/larger
python scripts/compress_session_by_id.py <session-id> --goal-size 80MB
python scripts/compress_session_by_id.py <session-id> --goal-size 1GB

# If the filename suffix lookup fails, scan session_meta.id too
python scripts/compress_session_by_id.py <session-id> --scan-meta --dry-run

# Also search archived_sessions next to the sessions base
python scripts/compress_session_by_id.py <session-id> --include-archived --dry-run

# Strict semantic verification without modifying the rollout
python scripts/compress_session_by_id.py <session-id> --verify-only

# Machine-readable compression or verification result
python scripts/compress_session_by_id.py <session-id> --dry-run --json
```

The compressor locates matching files under `CODEX_HOME/sessions` when
`CODEX_HOME` is set, otherwise under `~/.codex/sessions`. It chooses the
largest/newest match and delegates to `repair_rollout.py`. If `--base` points
at a non-default `sessions` or `archived_sessions` directory, the wrapper
infers that directory's parent as the Codex home for read-only Codex CLI
confirmation and optional `codex resume` checks; override it explicitly with
`--codex-home PATH` when needed.

When the user specifies a target in natural language, translate it to
`--goal-size`:

```text
"压到 80MB" -> --goal-size 80MB
"目标 1GB"  -> --goal-size 1GB
"默认压缩"  -> omit it; default is 100MB
```

Direct `repair_rollout.py --auto` usage follows the same default base directory
and can also be overridden with `--base PATH`.

## Compression strategy

Default compression is semantic-first:

1. Find the newest full compacted checkpoint: a `compacted` rollout item whose
   payload has `replacement_history`.
2. Preserve the first `session_meta`, that full `CompactedItem`, and every
   rollout line after it. This is the Codex resume reconstruction base and
   `rollout_suffix`.
3. Use the remaining `GOAL_SIZE` budget for historical breadcrumbs before that
   checkpoint: App-visible `event_msg` lines plus pre-checkpoint
   `turn_context` lines are selected first from both ends of the
   pre-checkpoint range, alternating from the first visible history side and
   the tail before compaction. The `event_msg` lines are the important App
   transcript source; `turn_context` is kept in the same UI-breadcrumb priority
   pool to preserve rollout structure for raw-history inspection and future
   replay strategies.
4. If budget remains after UI breadcrumbs, retain raw `response_item` lines from
   both ends as additional model-detail breadcrumbs. These can include messages,
   reasoning, function calls, function outputs, and image-bearing response
   items, but they do not replace the newest full checkpoint as the active
   resume baseline.
5. Represent the omitted historical middle with a tiny explicit synthetic
   maintenance turn made of timestamped Codex `RolloutLine` records:
   `event_msg.task_started`, `event_msg.user_message`,
   `event_msg.agent_message`, and `event_msg.task_complete`. The placeholder
   turn is inserted at the pre-checkpoint elision gap: after the retained
   left-side old breadcrumbs and before the retained right-side old breadcrumbs
   plus the newest full checkpoint. The synthetic `user_message` has a stable
   `client_id` prefix `codex-session-compress-elision-` and clearly says it was
   written by the compression tool, not by the original user. The injected text
   follows the operating system language when detectable: Chinese locales use
   Chinese text, otherwise English is used. Because this synthetic user message is
   before the newest full checkpoint, active Codex resume history remains based
   on that checkpoint and the following suffix.

When recompressing a rollout that already contains older
`codex-session-compress` synthetic markers before the newest full checkpoint,
those old marker events are omitted from the breadcrumb budget. The rewritten
rollout emits one current four-event synthetic maintenance turn for the new
physical elision instead of preserving stale or legacy marker fragments.

The mandatory checkpoint segment is preserved even when it exceeds
`GOAL_SIZE`. In that case the old pre-checkpoint history is reduced to the first
user task message or first App-visible user message, and the synthetic
maintenance turn is still emitted in the pre-checkpoint elision gap.

If no full compacted checkpoint exists, the compressor refuses to modify the
file. Ask the user to run `/compact` in Codex first so the rollout contains a
modern `CompactedItem.replacement_history` baseline.

Do not alter images or tool outputs in the active suffix after the newest full
compacted checkpoint unless the user explicitly accepts semantic loss.

## Verification and Codex CLI check

After repair, `compress_session_by_id.py` automatically runs:

```bash
python scripts/verify_rollout.py <rollout-path>
```

This checks valid UTF-8, valid JSON, `session_meta.id`, compacted checkpoint
presence when required, full compacted checkpoint presence when required,
embedded image policy, synthetic compression marker placement before the newest
full checkpoint, strict four-event synthetic maintenance turn shape, file size
under the V8 single-string ceiling, and reports active suffix
function-call/function-call-output `call_id` mismatches that Codex
`normalize_history` will repair before model input. When the
rollout was above `GOAL_SIZE` and semantic compression actually ran, the wrapper
runs semantic verification with:

```bash
python scripts/verify_rollout.py <rollout-path> \
  --require-compacted \
  --require-full-compacted \
  --semantic-checkpoint \
  --allow-active-images
```

`--allow-active-images` is intentional for semantic checkpoint cut: images
inside the latest full checkpoint, images after the latest full checkpoint, and
images before that checkpoint preserved as original historical breadcrumbs
(`event_msg`, `turn_context`, or `response_item`) may be part of the retained
checkpoint, active suffix, UI transcript, or raw history budget. Images in other
locations remain a verification failure.

After a real compression and successful static verification,
`compress_session_by_id.py` also runs:

```bash
python scripts/confirm_session_with_codex_cli.py <session-id> \
  --require-synthetic-marker
```

This launches `codex app-server --stdio` under the selected Codex home, sends
only `initialize` and read-only `thread/read` with `includeTurns=true`, and
confirms that Codex can reconstruct the thread history. The checker uses
threaded stdout/stderr readers instead of platform-specific `select`, so the
smoke check is friendlier to non-macOS platforms and avoids stderr pipe
back-pressure. When the compressed rollout contains the synthetic maintenance
turn, the confirmation also requires both visible synthetic messages in the same
reconstructed thread turn: the
`userMessage` with `clientId` prefix `codex-session-compress-elision-` and the
`agentMessage` containing `codex-session-compress`. It does not send
`thread/resume`, `turn/start`, or a prompt, so it should not append a new turn
or consume model usage.

Only skip this default confirmation when explicitly requested:

```bash
python scripts/compress_session_by_id.py <session-id> --skip-codex-cli-confirm
```

For an optional Codex CLI reload check, use:

```bash
python scripts/compress_session_by_id.py <session-id> --codex-check resume
```

This runs:

```bash
codex resume <session-id>
```

That check is interactive. Use it only when the user is ready to reopen the
session. Do not run a non-interactive prompt-based Codex check unless the user
explicitly agrees, because it may append a new turn to the session and consume
usage.

## Backup and cleanup

Before replacing a rollout, the repair script first writes the slimmed JSONL to
a temporary file, then renames the original file to a backup and installs the
slimmed file in the original path:

```text
<rollout-file>.orig
<rollout-file>.orig.1
<rollout-file>.orig.2
```

This avoids creating an extra full-size copy of multi-GB rollouts during normal
compression. Peak disk use is approximately the renamed original backup plus
the new slimmed file, not original plus copied backup plus slimmed file. A
custom `--backup-dir` must be on the same filesystem; otherwise the script
refuses instead of silently copying the full rollout.

Compression backups are intentionally preserved during compression. To inspect
backups for a session:

```bash
python scripts/compress_session_by_id.py <session-id> --list-backups
```

After `verify_rollout.py` and Codex CLI `thread/read` confirmation pass, and
the user confirms that Codex can reopen the session normally, use
`cleanup_compression_backups.py` to remove compression backups. It is dry-run by
default:

```bash
python scripts/cleanup_compression_backups.py <session-id>
```

Apply deletion only after explicit confirmation:

```bash
python scripts/cleanup_compression_backups.py <session-id> --apply --yes
```

To remove every compression backup under the selected `CODEX_HOME/sessions`
base after the user explicitly confirms all compressed sessions are good:

```bash
python scripts/cleanup_compression_backups.py --all --apply --yes
```

The cleanup script only targets `rollout-*.jsonl.orig*` under the sessions
base; it does not delete active JSONL files, cleanup manifests, or SQLite
state. If anything looks wrong before backup cleanup, restore with Codex closed:

```bash
mv '<backup-path>' '<rollout-path>'
```

## Recommended agent workflow

1. Run `python scripts/list_rollouts.py --top N` and show the largest sessions.
2. Ask the user which session ID to compress.
3. Run `python scripts/compress_session_by_id.py <session-id> --dry-run`.
4. If the preview looks appropriate, run `python scripts/compress_session_by_id.py <session-id> --goal-size <GOAL_SIZE>` or omit the option for 100MB.
5. Confirm `verify_rollout.py` reports `safe to load` and the automatic Codex
   CLI `thread/read` confirmation succeeds, including the synthetic
   maintenance turn when one was injected.
6. If requested and appropriate, run `python scripts/compress_session_by_id.py <session-id> --codex-check resume` or tell the user to run `codex resume <session-id>` manually.
7. Only after the user confirms the repaired session works, run
   `cleanup_compression_backups.py <session-id> --apply --yes` or, for a
   confirmed batch, `cleanup_compression_backups.py --all --apply --yes`.

For finished sub-agent deletion:

1. Confirm the target row is marked as a closed sub-agent, usually via
   `python scripts/list_rollouts.py --top N --show-title`.
2. Run `python scripts/cleanup_session_by_id.py <session-id>` and show the
   dry-run plan, including any spawned descendants that will be cleaned with
   the requested root session.
3. Ask the user to close Codex if the target session or parent thread may still
   be open.
4. Apply with `python scripts/cleanup_session_by_id.py <session-id> --apply --yes`.
   If the user explicitly says no backup / direct cleanup, add
   `--no-cleanup-backup`.
5. Report the backup manifest path when a backup was created, or clearly state
   that direct cleanup is irreversible. Always report SQLite integrity results.

For timed-out SubAgent cleanup:

1. Treat "清理超时 subagent" / "清理超时 SubAgent" as a request to clean both
   closed SubAgents and timed-out open/unknown SubAgents.
2. Gather current SubAgent IDs, then dry-run all of them with
   `cleanup_session_by_id.py ... --allow-timeout-subagent --timeout-hours 12`.
   Add `--allow-status-conflict` only when the dry-run shows conflicts from
   stale secondary state DB metadata and the user has asked to clean these
   SubAgents.
3. Inspect `plan.sessions[*].is_timeout_subagent` and
   `plan.sessions[*].timeout_subagent`: it reports parent thread ID, parent
   title, child last-active time, parent last-active time, and delta hours.
4. Apply cleanup only to the non-refused IDs from the dry-run. Do not use
   `--allow-open-subagent` for timeout cleanup; open/unknown SubAgents that are
   not timed out should remain refused.

## Safety rules

- Never compress or repair a rollout without a backup.
- Never remove compression backups without explicit user confirmation and
  `cleanup_compression_backups.py --apply --yes`.
- Never apply session cleanup without first showing a dry-run plan.
- Never use `--no-cleanup-backup` unless the user explicitly asks for no backup
  or direct irreversible cleanup.
- Never clean an open or unknown-state root sub-agent unless the user explicitly
  asks and either `--allow-timeout-subagent` confirms it is timed out or
  `--allow-open-subagent` is used.
- Never classify an open/unknown root sub-agent as timed out unless the parent
  thread's canonical SQLite last-active time is at least `--timeout-hours`
  newer than the child thread's last-active time.
- Never clean a root sub-agent with conflicting spawn statuses unless the user
  explicitly asks and `--allow-status-conflict` is used.
- Never clean a normal root session unless the user explicitly asks and
  `--allow-non-subagent` is used.
- Never remove the newest `compacted` record.
- Prefer the newest full compacted checkpoint over arbitrary newest compacted records.
- Never alter images or tool outputs in the active suffix.
- Never use non-checkpoint turn cutting; without a full compacted checkpoint, stop.
- After real compression, run Codex CLI/app-server confirmation unless the user
  explicitly asks to skip it.
- If verification fails, stop and restore from backup or ask for manual review.
