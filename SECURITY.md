# Security Policy

## Scope

This project runs locally and manages Codex session rollout files under `CODEX_HOME/sessions` or `~/.codex/sessions`. The scripts use Python standard library only and do not upload session data.

## Sensitive data

Codex rollout files can contain private prompts, model responses, tool outputs, local paths, screenshots, and other private project details.

Please follow these rules:

- Do not commit real `rollout-*.jsonl` files.
- Do not commit `.orig` backups created by the repair script.
- Do not commit screenshots, extracted image payloads, or debug logs from real sessions.
- Use small synthetic JSONL fixtures when reporting bugs.
- Redact private project details before sharing examples.

## Safety rules

- Always create a backup before modifying a rollout.
- Never delete compression backups without explicit user confirmation and `cleanup_compression_backups.py --apply --yes`; inspect the dry-run plan first.
- For session cleanup, always inspect the dry-run plan before using `--apply --yes`.
- Session cleanup is conservative by default: closed SubAgent roots are allowed; timed-out open/unknown SubAgents require explicit user intent plus `--allow-timeout-subagent`; ordinary threads require `--allow-non-subagent`.
- Use `--allow-status-conflict` only after the dry-run shows the conflict comes from stale secondary state DB metadata and the user explicitly accepts cleanup.
- Use `--no-cleanup-backup` only when the user explicitly asks for direct irreversible cleanup.
- Close Codex before applying cleanup or restoring files when the target session may still be open.
- Use `--allow-running-codex` only when the user explicitly accepts the risk of mutating files or SQLite state while a matching Codex home may still be active.
- If verification fails, stop and restore from backup before continuing.
- Close Codex before restoring a repaired rollout from backup.

## Reporting issues

When reporting a security or data-safety issue, include the affected script, version, minimal reproduction steps, expected behavior, and actual behavior. Use synthetic or redacted files only.
