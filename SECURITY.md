# Security Policy

## Scope

This project runs locally and manages Codex session rollout files under `~/.codex/sessions`. The scripts use Python standard library only and do not upload session data.

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
- Close Codex before applying cleanup or restoring files when the target session may still be open.
- If verification fails, stop and restore from backup before continuing.
- Close Codex before restoring a repaired rollout from backup.

## Reporting issues

When reporting a security or data-safety issue, include the affected script, version, minimal reproduction steps, expected behavior, and actual behavior. Use synthetic or redacted files only.
