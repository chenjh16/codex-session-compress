#!/usr/bin/env python3
"""Synthetic smoke tests for codex-session-compress scripts.

These tests use tiny generated JSONL files only. They do not read or modify
~/.codex/sessions.
"""

import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SESSION_ID = "00000000-0000-0000-0000-000000000000"
FALLBACK_SESSION_ID = "11111111-1111-1111-1111-111111111111"
MERGED_SQLITE_SESSION_ID = "22222222-2222-2222-2222-222222222222"
SUBAGENT_SESSION_ID = "33333333-3333-3333-3333-333333333333"
OPEN_SUBAGENT_SESSION_ID = "44444444-4444-4444-4444-444444444444"
CHILD_SUBAGENT_SESSION_ID = "55555555-5555-5555-5555-555555555555"
GRANDCHILD_SUBAGENT_SESSION_ID = "66666666-6666-6666-6666-666666666666"
CONFLICT_SUBAGENT_SESSION_ID = "77777777-7777-7777-7777-777777777777"
STALE_DESCENDANT_SESSION_ID = "88888888-8888-8888-8888-888888888888"


def timestamped_rollout_row(row, index):
    if "timestamp" in row:
        return row
    return {"timestamp": "2026-01-01T00:00:%02d.000Z" % (index % 60), **row}


def write_rollout_rows(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows):
            handle.write(json.dumps(timestamped_rollout_row(row, index), separators=(",", ":")) + "\n")


def load_cleanup_module():
    spec = importlib.util.spec_from_file_location("cleanup_session_by_id", SCRIPTS / "cleanup_session_by_id.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_compress_module():
    spec = importlib.util.spec_from_file_location("compress_session_by_id", SCRIPTS / "compress_session_by_id.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_confirm_module():
    spec = importlib.util.spec_from_file_location("confirm_session_with_codex_cli", SCRIPTS / "confirm_session_with_codex_cli.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, session_id: str = SESSION_ID) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/project"}},
        {"type": "turn_context", "payload": {"cwd": "/tmp/project"}},
        {"type": "event_msg", "payload": {"message": "preamble"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "start"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "working"}]}},
        {"type": "compacted", "payload": {"message": "old", "replacement_history": []}},
        {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary anchor"}]}]}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{}"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "x" * 40000}},
        {"type": "event_msg", "payload": {"type": "image", "data": "AAAABBBB", "mimeType": "image/jpeg"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "recent"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    write_rollout_rows(path, rows)


def write_breadcrumb_jsonl(path: Path, session_id: str = SESSION_ID) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/project"}},
        {"type": "event_msg", "payload": {"message": "large old UI-only event", "blob": "x" * 50000}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "start"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "old-call", "name": "shell", "arguments": "{}"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "old-call", "output": "RAW_TOOL_OUTPUT_SHOULD_REMAIN"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]}},
        {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary anchor"}]}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "recent"}]}},
    ]
    write_rollout_rows(path, rows)


def write_ui_breadcrumb_jsonl(path: Path, session_id: str = SESSION_ID) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/project"}},
        {"type": "turn_context", "payload": {"cwd": "/tmp/project", "model": "gpt-test"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "APP_VISIBLE_START", "images": [], "local_images": [], "text_elements": []}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "raw start"}]}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "APP_VISIBLE_AGENT"}},
        {"type": "event_msg", "payload": {"type": "token_count", "blob": "x" * 50000, "message": "LARGE_UI_EVENT_SHOULD_DROP"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "old-huge", "output": "RAW_MODEL_BLOB_SHOULD_DROP" * 4000}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "APP_VISIBLE_TAIL"}},
        {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary anchor"}]}]}},
        {"type": "event_msg", "payload": {"type": "context_compacted"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "recent"}]}},
    ]
    write_rollout_rows(path, rows)


def write_legacy_compression_marker_jsonl(path: Path, session_id: str = SESSION_ID) -> None:
    legacy_turn_id = "codex-session-compress-elision-legacy"
    rows = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/project"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "APP_VISIBLE_START", "images": [], "local_images": [], "text_elements": []}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "APP_VISIBLE_LEFT_AGENT"}},
        {"type": "event_msg", "payload": {"type": "token_count", "blob": "x" * 50000, "message": "LARGE_UI_EVENT_SHOULD_DROP"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": legacy_turn_id}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "[codex-session-compress: omitted old history before current marker]"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": legacy_turn_id,
                "last_agent_message": "[codex-session-compress: omitted old history before current marker]",
            },
        },
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "APP_VISIBLE_RIGHT_AGENT"}},
        {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary anchor"}]}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "recent"}]}},
    ]
    write_rollout_rows(path, rows)


def write_nonbreadcrumb_omission_jsonl(path: Path, session_id: str = SESSION_ID) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/project"}},
        {"type": "world_state", "payload": {"blob": "NON_BREADCRUMB_HISTORY_SHOULD_DROP" * 1000}},
        {
            "type": "compacted",
            "payload": {
                "message": "new",
                "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary anchor"}]}],
            },
        },
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "recent"}]}},
    ]
    write_rollout_rows(path, rows)


def write_subagent_jsonl(path: Path, session_id: str = SUBAGENT_SESSION_ID) -> None:
    rows = [
        {
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "cwd": "/tmp/parent-project",
                "thread_source": "subagent",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "agent_nickname": "Kepler",
                            "agent_role": "inspect one subsystem",
                        }
                    }
                },
            },
        },
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "child work"}]}},
    ]
    write_rollout_rows(path, rows)


def compacted_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if json.loads(line).get("type") == "compacted":
                count += 1
    return count


def newest_compacted_message(path: Path) -> str:
    message = ""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") == "compacted":
                message = (obj.get("payload") or {}).get("message", "")
    return message


def run(cmd, env=None):
    return subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_repair_and_verify_synthetic_rollout():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        write_jsonl(rollout)
        original_inode = rollout.stat().st_ino

        dry = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "10KB", "--dry-run", "--json"])
        assert dry.returncode == 0, dry.stderr + dry.stdout
        dry_plan = json.loads(dry.stdout)
        assert dry_plan["dry_run"] is True
        assert dry_plan["checkpoint_line"] == 7
        assert dry_plan["historical_response_items_omitted"] >= 0

        repair = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "10KB"])
        assert repair.returncode == 0, repair.stderr + repair.stdout
        backup = Path(str(rollout) + ".orig")
        assert backup.exists()
        assert backup.stat().st_ino == original_inode
        assert rollout.stat().st_ino != original_inode

        # The semantic checkpoint cut should drop old compacted checkpoints but
        # keep the newest full one, even when the mandatory suffix exceeds the
        # goal size.
        assert compacted_count(rollout) == 1
        assert newest_compacted_message(rollout) == "new"

        verify = run(
            [
                sys.executable,
                str(SCRIPTS / "verify_rollout.py"),
                str(rollout),
                "--limit-mb",
                "1",
                "--require-compacted",
                "--require-full-compacted",
                "--semantic-checkpoint",
                "--allow-active-images",
            ]
        )
        assert verify.returncode == 0, verify.stderr + verify.stdout
        assert "safe to load" in verify.stdout


def test_verify_can_require_compacted():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}},
        ]
        write_rollout_rows(rollout, rows)
        verify = run([sys.executable, str(SCRIPTS / "verify_rollout.py"), str(rollout), "--require-compacted"])
        assert verify.returncode == 1
        assert "compacted checkpoint" in verify.stdout


def test_semantic_verify_requires_full_compacted():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}},
        ]
        write_rollout_rows(rollout, rows)
        verify = run([sys.executable, str(SCRIPTS / "verify_rollout.py"), str(rollout), "--semantic-checkpoint"])
        assert verify.returncode == 1
        assert "full compacted checkpoint" in verify.stdout


def test_semantic_verify_reports_mismatched_active_call_ids_as_normalized():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary"}]}]}},
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "call-a", "name": "shell", "arguments": "{}"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-b", "output": "wrong"}},
        ]
        write_rollout_rows(rollout, rows)
        verify = run([sys.executable, str(SCRIPTS / "verify_rollout.py"), str(rollout), "--semantic-checkpoint"])
        assert verify.returncode == 0
        assert "function_call/function_call_output normalization" in verify.stdout
        assert "Codex normalize_history will repair" in verify.stdout
        assert "missing outputs for call-a" in verify.stdout


def test_semantic_verify_rejects_synthetic_user_marker_after_full_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        turn_id = "codex-session-compress-elision-2"
        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary"}]}]}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": turn_id, "message": "synthetic marker"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn_id}},
        ]
        write_rollout_rows(rollout, rows)
        verify = run([sys.executable, str(SCRIPTS / "verify_rollout.py"), str(rollout), "--semantic-checkpoint"])
        assert verify.returncode == 1
        assert "synthetic compression marker stays before newest full checkpoint" in verify.stdout


def test_semantic_verify_rejects_malformed_synthetic_maintenance_turn():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        turn_id = "codex-session-compress-elision-2"
        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": turn_id, "message": "synthetic marker"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn_id}},
            {"type": "compacted", "payload": {"message": "new", "replacement_history": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "summary"}]}]}},
        ]
        write_rollout_rows(rollout, rows)
        verify = run([sys.executable, str(SCRIPTS / "verify_rollout.py"), str(rollout), "--semantic-checkpoint"])
        assert verify.returncode == 1
        assert "synthetic maintenance turn shape" in verify.stdout
        assert "expected agent_message" in verify.stdout


def test_semantic_verify_allows_full_checkpoint_images():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {
                "type": "compacted",
                "payload": {
                    "message": "new",
                    "replacement_history": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_image", "image_url": "data:image/png;base64,BBBB"}],
                        }
                    ],
                },
            },
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "recent"}]}},
        ]
        write_rollout_rows(rollout, rows)
        verify = run(
            [
                sys.executable,
                str(SCRIPTS / "verify_rollout.py"),
                str(rollout),
                "--semantic-checkpoint",
                "--allow-active-images",
                "--json",
            ]
        )
        assert verify.returncode == 0, verify.stderr + verify.stdout
        data = json.loads(verify.stdout)
        assert data["images"]["checkpoint"] == 1
        assert data["images"]["disallowed"] == 0


def test_breadcrumb_preserves_original_response_items_within_goal():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        write_breadcrumb_jsonl(rollout)

        repair = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "30KB"])
        assert repair.returncode == 0, repair.stderr + repair.stdout
        text = rollout.read_text(encoding="utf-8")
        assert "RAW_TOOL_OUTPUT_SHOULD_REMAIN" in text
        assert "data:image/png;base64,AAAA" in text
        assert "large old UI-only event" not in text

        verify = run(
            [
                sys.executable,
                str(SCRIPTS / "verify_rollout.py"),
                str(rollout),
                "--semantic-checkpoint",
                "--allow-active-images",
            ]
        )
        assert verify.returncode == 0, verify.stderr + verify.stdout
        assert "retained historical breadcrumbs" in verify.stdout


def test_breadcrumb_preserves_app_visible_event_and_turn_context_items_first():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        write_ui_breadcrumb_jsonl(rollout)

        dry = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "8KB", "--dry-run", "--json"])
        assert dry.returncode == 0, dry.stderr + dry.stdout
        plan = json.loads(dry.stdout)
        assert plan["historical_ui_items_kept"] >= 3
        assert plan["historical_ui_items_candidates"] >= plan["historical_ui_items_kept"]
        assert plan["placeholder_type"] == "event_msg.synthetic_maintenance_turn.user_message_agent_message"
        assert plan["placeholder_line_count"] == 4
        assert plan["placeholder_position"] == "pre-checkpoint-elision-gap"
        assert plan["placeholder_language"] in ("en", "zh")

        repair = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "8KB"])
        assert repair.returncode == 0, repair.stderr + repair.stdout
        rows = [json.loads(line) for line in rollout.read_text(encoding="utf-8").splitlines() if line.strip()]
        text = json.dumps(rows, ensure_ascii=False)
        assert rows[0]["type"] == "session_meta"
        compacted_index = next(index for index, row in enumerate(rows) if row.get("type") == "compacted")
        context_index = next(
            index
            for index, row in enumerate(rows)
            if row.get("type") == "event_msg" and (row.get("payload") or {}).get("type") == "context_compacted"
        )
        placeholder_index = next(
            index
            for index, row in enumerate(rows)
            if row.get("type") == "event_msg" and (row.get("payload") or {}).get("type") == "task_started"
            and ((row.get("payload") or {}).get("turn_id") or "").startswith("codex-session-compress-elision-")
        )
        assert placeholder_index < compacted_index < context_index
        assert all(rows[placeholder_index + offset].get("timestamp") for offset in range(4))
        assert (rows[placeholder_index + 1].get("payload") or {}).get("type") == "user_message"
        assert ((rows[placeholder_index + 1].get("payload") or {}).get("client_id") or "").startswith(
            "codex-session-compress-elision-"
        )
        assert "codex-session-compress" in ((rows[placeholder_index + 1].get("payload") or {}).get("message") or "")
        assert (rows[placeholder_index + 2].get("payload") or {}).get("type") == "agent_message"
        assert "codex-session-compress" in ((rows[placeholder_index + 2].get("payload") or {}).get("message") or "")
        assert (rows[placeholder_index + 3].get("payload") or {}).get("type") == "task_complete"
        assert "APP_VISIBLE_START" in text
        assert "APP_VISIBLE_AGENT" in text
        assert "APP_VISIBLE_TAIL" in text
        assert '"type": "turn_context"' in text
        assert "codex-session-compress" in text
        assert "LARGE_UI_EVENT_SHOULD_DROP" not in text
        assert "RAW_MODEL_BLOB_SHOULD_DROP" not in text
        assert any(row.get("type") == "event_msg" and (row.get("payload") or {}).get("type") == "agent_message" for row in rows)

        verify = run(
            [
                sys.executable,
                str(SCRIPTS / "verify_rollout.py"),
                str(rollout),
                "--semantic-checkpoint",
                "--allow-active-images",
            ]
        )
        assert verify.returncode == 0, verify.stderr + verify.stdout


def test_physical_precheckpoint_omission_gets_placeholder_even_without_breadcrumb_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        write_nonbreadcrumb_omission_jsonl(rollout)

        dry = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "2KB", "--dry-run", "--json"])
        assert dry.returncode == 0, dry.stderr + dry.stdout
        plan = json.loads(dry.stdout)
        assert plan["historical_breadcrumb_items_candidates"] == 0
        assert plan["historical_breadcrumb_items_omitted"] == 0
        assert plan["historical_rollout_lines_omitted"] == 1
        assert plan["placeholder_line_count"] == 4

        repair = run([sys.executable, str(SCRIPTS / "repair_rollout.py"), str(rollout), "--goal-size", "2KB"])
        assert repair.returncode == 0, repair.stderr + repair.stdout
        text = rollout.read_text(encoding="utf-8")
        assert "codex-session-compress" in text
        assert "NON_BREADCRUMB_HISTORY_SHOULD_DROP" not in text
        verify = run([sys.executable, str(SCRIPTS / "verify_rollout.py"), str(rollout), "--semantic-checkpoint"])
        assert verify.returncode == 0, verify.stderr + verify.stdout


def test_repair_omits_existing_precheckpoint_compression_marker_events():
    with tempfile.TemporaryDirectory() as tmp:
        rollout = Path(tmp) / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        write_legacy_compression_marker_jsonl(rollout)

        repair = run(
            [
                sys.executable,
                str(SCRIPTS / "repair_rollout.py"),
                str(rollout),
                "--goal-size",
                "12KB",
                "--json",
            ]
        )
        assert repair.returncode == 0, repair.stderr + repair.stdout
        plan = json.loads(repair.stdout)
        assert plan["historical_compression_marker_events_omitted"] == 3

        text = rollout.read_text(encoding="utf-8")
        assert "codex-session-compress-elision-legacy" not in text
        assert "[codex-session-compress: omitted old history" not in text

        verify = run(
            [
                sys.executable,
                str(SCRIPTS / "verify_rollout.py"),
                str(rollout),
                "--require-compacted",
                "--require-full-compacted",
                "--semantic-checkpoint",
            ]
        )
        assert verify.returncode == 0, verify.stderr + verify.stdout


def test_list_rollouts_can_show_app_titles():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        first = sessions / f"rollout-2026-01-01T00-00-00-{SESSION_ID}.jsonl"
        second = sessions / f"rollout-2026-01-01T00-00-01-{FALLBACK_SESSION_ID}.jsonl"
        third = sessions / f"rollout-2026-01-01T00-00-02-{MERGED_SQLITE_SESSION_ID}.jsonl"
        fourth = sessions / f"rollout-2026-01-01T00-00-03-{SUBAGENT_SESSION_ID}.jsonl"
        write_jsonl(first, SESSION_ID)
        write_jsonl(second, FALLBACK_SESSION_ID)
        write_jsonl(third, MERGED_SQLITE_SESSION_ID)
        write_jsonl(fourth, SUBAGENT_SESSION_ID)

        with (home / "session_index.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": SESSION_ID, "thread_name": "Old title", "updated_at": "2026-01-01T00:00:00Z"}) + "\n")
            handle.write(json.dumps({"id": SESSION_ID, "thread_name": "App Display Title", "updated_at": "2026-01-01T00:01:00Z"}) + "\n")
            handle.write(json.dumps({"id": SUBAGENT_SESSION_ID, "thread_name": "Very long first user prompt that should not win"}) + "\n")

        conn = sqlite3.connect(str(home / "state_6.sqlite"))
        try:
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT)"
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (FALLBACK_SESSION_ID, "SQLite Fallback Title", None, None, "user", "vscode"),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SUBAGENT_SESSION_ID,
                    "Very long first user prompt that should not be shown as the App title",
                    "Kepler",
                    None,
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Kepler"}}}',
                ),
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(str(home / "state_5.sqlite"))
        try:
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
            conn.execute("INSERT INTO threads (id, title) VALUES (?, ?)", (MERGED_SQLITE_SESSION_ID, "Merged SQLite Title"))
            conn.commit()
        finally:
            conn.close()

        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        result = run([sys.executable, str(SCRIPTS / "list_rollouts.py"), "--top", "0", "--show-title", "--json"], env=env)
        assert result.returncode == 0, result.stderr + result.stdout
        data = json.loads(result.stdout)
        by_id = {entry["session_id"]: entry for entry in data["entries"]}
        assert by_id[SESSION_ID]["title"] == "App Display Title"
        assert by_id[SESSION_ID]["title_source"] == "session_index"
        assert by_id[SESSION_ID]["project"] == "project"
        assert by_id[SESSION_ID]["project_dir_name"] == "project"
        assert by_id[FALLBACK_SESSION_ID]["title"] == "SQLite Fallback Title"
        assert by_id[FALLBACK_SESSION_ID]["title_source"] == "sqlite"
        assert by_id[MERGED_SQLITE_SESSION_ID]["title"] == "Merged SQLite Title"
        assert by_id[MERGED_SQLITE_SESSION_ID]["title_source"] == "sqlite"
        assert by_id[SUBAGENT_SESSION_ID]["title"] == "【Sub】Kepler"
        assert by_id[SUBAGENT_SESSION_ID]["title_source"] == "sqlite_agent_nickname"
        assert by_id[SUBAGENT_SESSION_ID]["is_subagent"] is True
        assert by_id[SUBAGENT_SESSION_ID]["agent_nickname"] == "Kepler"
        assert "title_raw" not in by_id[SUBAGENT_SESSION_ID]

        table = run([sys.executable, str(SCRIPTS / "list_rollouts.py"), "--top", "1"], env=env)
        assert table.returncode == 0, table.stderr + table.stdout
        assert "project" in table.stdout


def test_list_rollouts_explicit_base_controls_title_home_over_env_codex_home():
    with tempfile.TemporaryDirectory() as tmp:
        selected_home = Path(tmp) / "selected-home"
        env_home = Path(tmp) / "env-home"
        sessions = selected_home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        (env_home / "sessions").mkdir(parents=True)
        rollout = sessions / f"rollout-2026-01-01T00-00-00-{SESSION_ID}.jsonl"
        write_jsonl(rollout)

        with (selected_home / "session_index.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": SESSION_ID, "thread_name": "Selected Home Title"}) + "\n")
        with (env_home / "session_index.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": SESSION_ID, "thread_name": "Wrong Env Title"}) + "\n")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(env_home)
        result = run(
            [
                sys.executable,
                str(SCRIPTS / "list_rollouts.py"),
                "--base",
                str(selected_home / "sessions"),
                "--top",
                "0",
                "--show-title",
                "--json",
            ],
            env=env,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        data = json.loads(result.stdout)
        assert data["entries"][0]["title"] == "Selected Home Title"


def test_repair_auto_json_is_machine_readable_without_banner_text():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "sessions"
        day = base / "2026" / "01" / "01"
        day.mkdir(parents=True)
        rollout = day / f"rollout-20260101T000000-{SESSION_ID}.jsonl"
        write_nonbreadcrumb_omission_jsonl(rollout)

        result = run(
            [
                sys.executable,
                str(SCRIPTS / "repair_rollout.py"),
                "--auto",
                "--base",
                str(base),
                "--goal-size",
                "2KB",
                "--dry-run",
                "--json",
            ]
        )
        assert result.returncode == 0, result.stderr + result.stdout
        data = json.loads(result.stdout)
        assert data["auto"] is True
        assert data["path"] == str(rollout)
        assert "Oversized rollouts" not in result.stdout


def test_compress_session_verify_only_json():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        rollout = sessions / f"rollout-2026-01-01T00-00-00-{SESSION_ID}.jsonl"
        write_jsonl(rollout)

        result = run(
            [
                sys.executable,
                str(SCRIPTS / "compress_session_by_id.py"),
                SESSION_ID,
                "--base",
                str(home / "sessions"),
                "--verify-only",
                "--json",
            ]
        )
        assert result.returncode == 0, result.stderr + result.stdout
        data = json.loads(result.stdout)
        assert data["mode"] == "verify_only"
        assert data["verify"]["ok"] is True
        assert data["verify"]["full_compacted_count"] == 2


def test_compress_session_reports_inferred_codex_home_for_base():
    compress = load_compress_module()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        rollout = sessions / f"rollout-2026-01-01T00-00-00-{SESSION_ID}.jsonl"
        write_jsonl(rollout)

        assert compress.infer_codex_home_from_base(home / "sessions") == home.resolve()
        result = run(
            [
                sys.executable,
                str(SCRIPTS / "compress_session_by_id.py"),
                SESSION_ID,
                "--base",
                str(home / "sessions"),
                "--dry-run",
                "--json",
            ]
        )
        assert result.returncode == 0, result.stderr + result.stdout
        data = json.loads(result.stdout)
        assert data["codex_home"] == str(home.resolve())


def test_cleanup_session_by_id_removes_subagent_artifacts_only_after_apply():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        sub_rollout = sessions / f"rollout-2026-01-01T00-00-03-{SUBAGENT_SESSION_ID}.jsonl"
        normal_rollout = sessions / f"rollout-2026-01-01T00-00-04-{FALLBACK_SESSION_ID}.jsonl"
        child_rollout = sessions / f"rollout-2026-01-01T00-00-05-{CHILD_SUBAGENT_SESSION_ID}.jsonl"
        grandchild_rollout = sessions / f"rollout-2026-01-01T00-00-06-{GRANDCHILD_SUBAGENT_SESSION_ID}.jsonl"
        write_subagent_jsonl(sub_rollout)
        write_subagent_jsonl(child_rollout, CHILD_SUBAGENT_SESSION_ID)
        write_subagent_jsonl(grandchild_rollout, GRANDCHILD_SUBAGENT_SESSION_ID)
        write_jsonl(normal_rollout, FALLBACK_SESSION_ID)
        sub_compressed = sub_rollout.with_suffix(".jsonl.zst")
        sub_compressed.write_bytes(b"compressed sibling")
        sub_backup = Path(str(sub_rollout) + ".orig")
        sub_backup.write_text("old backup\n", encoding="utf-8")

        with (home / "session_index.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": SUBAGENT_SESSION_ID, "thread_name": "Kepler"}) + "\n")
            handle.write(json.dumps({"id": CHILD_SUBAGENT_SESSION_ID, "thread_name": "Child"}) + "\n")
            handle.write(json.dumps({"id": GRANDCHILD_SUBAGENT_SESSION_ID, "thread_name": "Grandchild"}) + "\n")
            handle.write(json.dumps({"id": FALLBACK_SESSION_ID, "thread_name": "Normal"}) + "\n")

        state_db = home / "state_6.sqlite"
        conn = sqlite3.connect(str(state_db))
        try:
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT)"
            )
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
            conn.execute("CREATE TABLE thread_dynamic_tools (thread_id TEXT, tool TEXT)")
            conn.execute("CREATE TABLE agent_job_items (assigned_thread_id TEXT, status TEXT, updated_at INTEGER, last_error TEXT, body TEXT)")
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SUBAGENT_SESSION_ID,
                    "Long child prompt",
                    "Kepler",
                    "inspect one subsystem",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Kepler"}}}',
                ),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (FALLBACK_SESSION_ID, "Normal", None, None, "user", "vscode"),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    OPEN_SUBAGENT_SESSION_ID,
                    "Open child prompt",
                    "OpenChild",
                    "still running",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"OpenChild"}}}',
                ),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    CHILD_SUBAGENT_SESSION_ID,
                    "Child prompt",
                    "Child",
                    "child worker",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Child"}}}',
                ),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    GRANDCHILD_SUBAGENT_SESSION_ID,
                    "Grandchild prompt",
                    "Grandchild",
                    "grandchild worker",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Grandchild"}}}',
                ),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    CONFLICT_SUBAGENT_SESSION_ID,
                    "Conflict child prompt",
                    "Conflict",
                    "conflicting status",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Conflict"}}}',
                ),
            )
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", ("parent", SUBAGENT_SESSION_ID, "closed"))
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", (SUBAGENT_SESSION_ID, CHILD_SUBAGENT_SESSION_ID, "closed"))
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", (CHILD_SUBAGENT_SESSION_ID, GRANDCHILD_SUBAGENT_SESSION_ID, "open"))
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", ("parent", OPEN_SUBAGENT_SESSION_ID, "open"))
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", ("parent", CONFLICT_SUBAGENT_SESSION_ID, "closed"))
            conn.execute("INSERT INTO thread_dynamic_tools (thread_id, tool) VALUES (?, ?)", (SUBAGENT_SESSION_ID, "tool"))
            conn.execute("INSERT INTO thread_dynamic_tools (thread_id, tool) VALUES (?, ?)", (CHILD_SUBAGENT_SESSION_ID, "child-tool"))
            conn.execute("INSERT INTO agent_job_items (assigned_thread_id, status, updated_at, last_error, body) VALUES (?, ?, ?, ?, ?)", (SUBAGENT_SESSION_ID, "running", 0, None, "job"))
            conn.execute("INSERT INTO agent_job_items (assigned_thread_id, status, updated_at, last_error, body) VALUES (?, ?, ?, ?, ?)", (GRANDCHILD_SUBAGENT_SESSION_ID, "pending", 0, None, "queued"))
            conn.execute("INSERT INTO thread_dynamic_tools (thread_id, tool) VALUES (?, ?)", (FALLBACK_SESSION_ID, "keep"))
            conn.commit()
        finally:
            conn.close()

        conflict_db = home / "state_7.sqlite"
        conn = sqlite3.connect(str(conflict_db))
        try:
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT)"
            )
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    CONFLICT_SUBAGENT_SESSION_ID,
                    "Conflict child prompt",
                    "Conflict",
                    "conflicting status",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Conflict"}}}',
                ),
            )
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", ("parent", CONFLICT_SUBAGENT_SESSION_ID, "open"))
            conn.commit()
        finally:
            conn.close()

        stale_db = home / "state_5.sqlite"
        conn = sqlite3.connect(str(stale_db))
        try:
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT)"
            )
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    STALE_DESCENDANT_SESSION_ID,
                    "Stale descendant",
                    "Stale",
                    "stale worker",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Stale"}}}',
                ),
            )
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", (SUBAGENT_SESSION_ID, STALE_DESCENDANT_SESSION_ID, "closed"))
            conn.commit()
        finally:
            conn.close()

        for db_name, table_name in (
            ("logs_2.sqlite", "logs"),
            ("goals_1.sqlite", "thread_goals"),
            ("memories_1.sqlite", "stage1_outputs"),
        ):
            conn = sqlite3.connect(str(home / db_name))
            try:
                conn.execute(f"CREATE TABLE {table_name} (thread_id TEXT, payload TEXT)")
                conn.execute(f"INSERT INTO {table_name} (thread_id, payload) VALUES (?, ?)", (SUBAGENT_SESSION_ID, "delete"))
                conn.execute(f"INSERT INTO {table_name} (thread_id, payload) VALUES (?, ?)", (CHILD_SUBAGENT_SESSION_ID, "delete-child"))
                conn.execute(f"INSERT INTO {table_name} (thread_id, payload) VALUES (?, ?)", (FALLBACK_SESSION_ID, "keep"))
                conn.commit()
            finally:
                conn.close()

        dry = run([sys.executable, str(SCRIPTS / "cleanup_session_by_id.py"), SUBAGENT_SESSION_ID, "--codex-home", str(home), "--json"])
        assert dry.returncode == 0, dry.stderr + dry.stdout
        plan = json.loads(dry.stdout)["plan"]
        resolved_state_db = str(state_db.resolve())
        resolved_sub_rollout = str(sub_rollout.resolve())
        assert plan["sessions"][SUBAGENT_SESSION_ID]["is_subagent"] is True
        assert plan["sessions"][SUBAGENT_SESSION_ID]["is_closed_subagent"] is True
        assert plan["sessions"][SUBAGENT_SESSION_ID]["spawn_edge_statuses"] == ["closed"]
        assert plan["requested_session_ids"] == [SUBAGENT_SESSION_ID]
        assert plan["descendant_session_ids"] == [CHILD_SUBAGENT_SESSION_ID, GRANDCHILD_SUBAGENT_SESSION_ID]
        assert plan["session_ids"] == [SUBAGENT_SESSION_ID, CHILD_SUBAGENT_SESSION_ID, GRANDCHILD_SUBAGENT_SESSION_ID]
        assert STALE_DESCENDANT_SESSION_ID in plan["stale_spawn_descendant_candidates"]
        assert STALE_DESCENDANT_SESSION_ID not in plan["session_ids"]
        assert plan["canonical_state_db"] == resolved_state_db
        assert plan["sessions"][CHILD_SUBAGENT_SESSION_ID]["included_by_spawn_tree"] is True
        assert plan["sessions"][GRANDCHILD_SUBAGENT_SESSION_ID]["included_by_spawn_tree"] is True
        assert plan["sessions"][GRANDCHILD_SUBAGENT_SESSION_ID]["spawn_edge_statuses"] == ["open"]
        assert plan["sessions"][GRANDCHILD_SUBAGENT_SESSION_ID]["status_warning"]
        assert len(plan["rollout_files"]) == 5
        assert plan["session_index"]["matching_lines"] == 3
        assert plan["sqlite_counts"][resolved_state_db] == {
            "agent_job_items_unassign": 2,
            "thread_dynamic_tools": 2,
            "thread_spawn_edges": 3,
            "threads": 3,
        }
        assert sub_rollout.exists()
        assert child_rollout.exists()
        assert grandchild_rollout.exists()
        assert sub_backup.exists()

        open_refused = run([sys.executable, str(SCRIPTS / "cleanup_session_by_id.py"), OPEN_SUBAGENT_SESSION_ID, "--codex-home", str(home), "--json"])
        assert open_refused.returncode == 1
        open_plan = json.loads(open_refused.stdout)["plan"]
        assert open_plan["sessions"][OPEN_SUBAGENT_SESSION_ID]["is_subagent"] is True
        assert open_plan["sessions"][OPEN_SUBAGENT_SESSION_ID]["spawn_edge_statuses"] == ["open"]
        assert open_plan["refused_not_closed"] == [OPEN_SUBAGENT_SESSION_ID]

        refused = run([sys.executable, str(SCRIPTS / "cleanup_session_by_id.py"), FALLBACK_SESSION_ID, "--codex-home", str(home), "--json"])
        assert refused.returncode == 1
        assert json.loads(refused.stdout)["plan"]["refused"] == [FALLBACK_SESSION_ID]

        conflict_refused = run([sys.executable, str(SCRIPTS / "cleanup_session_by_id.py"), CONFLICT_SUBAGENT_SESSION_ID, "--codex-home", str(home), "--json"])
        assert conflict_refused.returncode == 1
        conflict_plan = json.loads(conflict_refused.stdout)["plan"]
        assert conflict_plan["sessions"][CONFLICT_SUBAGENT_SESSION_ID]["has_spawn_edge_status_conflict"] is True
        assert conflict_plan["refused_status_conflict"] == [CONFLICT_SUBAGENT_SESSION_ID]

        backup_root = Path(tmp) / "cleanup-backups"
        applied = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_session_by_id.py"),
                SUBAGENT_SESSION_ID,
                "--codex-home",
                str(home),
                "--backup-root",
                str(backup_root),
                "--apply",
                "--yes",
                "--allow-running-codex",
                "--json",
            ]
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout
        manifest = json.loads(applied.stdout)["manifest"]
        assert Path(manifest["manifest_path"]).is_file()
        assert not sub_rollout.exists()
        assert not sub_compressed.exists()
        assert not sub_backup.exists()
        assert not child_rollout.exists()
        assert not grandchild_rollout.exists()
        assert normal_rollout.exists()
        assert any(item["source"] == resolved_sub_rollout for item in manifest["backups"])
        assert any(item["source"] == resolved_state_db for item in manifest["backups"])
        assert all(value == "ok" for value in manifest["integrity"].values())

        index_lines = (home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(index_lines) == 1
        assert FALLBACK_SESSION_ID in index_lines[0]
        assert SUBAGENT_SESSION_ID not in index_lines[0]
        assert CHILD_SUBAGENT_SESSION_ID not in index_lines[0]
        assert GRANDCHILD_SUBAGENT_SESSION_ID not in index_lines[0]

        conn = sqlite3.connect(str(state_db))
        try:
            assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (CHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (GRANDCHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (FALLBACK_SESSION_ID,)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM thread_dynamic_tools WHERE thread_id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_dynamic_tools WHERE thread_id = ?", (CHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_dynamic_tools WHERE thread_id = ?", (FALLBACK_SESSION_ID,)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM thread_spawn_edges WHERE child_thread_id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_spawn_edges WHERE child_thread_id = ?", (CHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_spawn_edges WHERE child_thread_id = ?", (GRANDCHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_spawn_edges WHERE child_thread_id = ?", (OPEN_SUBAGENT_SESSION_ID,)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM agent_job_items WHERE assigned_thread_id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM agent_job_items WHERE assigned_thread_id = ?", (GRANDCHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM agent_job_items WHERE assigned_thread_id IS NULL AND status = 'pending'").fetchone()[0] == 2
        finally:
            conn.close()

        for db_name, table_name in (
            ("logs_2.sqlite", "logs"),
            ("goals_1.sqlite", "thread_goals"),
            ("memories_1.sqlite", "stage1_outputs"),
        ):
            conn = sqlite3.connect(str(home / db_name))
            try:
                assert conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE thread_id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
                assert conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE thread_id = ?", (CHILD_SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
                assert conn.execute(f"SELECT COUNT(*) FROM {table_name} WHERE thread_id = ?", (FALLBACK_SESSION_ID,)).fetchone()[0] == 1
            finally:
                conn.close()

        restored = run(
            [
                sys.executable,
                str(SCRIPTS / "restore_cleanup_manifest.py"),
                manifest["manifest_path"],
                "--apply",
                "--yes",
                "--allow-running-codex",
                "--json",
            ]
        )
        assert restored.returncode == 0, restored.stderr + restored.stdout
        restore_data = json.loads(restored.stdout)
        assert restore_data["ok"] is True
        assert sub_rollout.exists()
        assert child_rollout.exists()
        assert grandchild_rollout.exists()
        assert sub_backup.exists()
        assert all(value == "ok" for value in restore_data["integrity"].values())


def test_cleanup_allows_timed_out_open_subagent_only_with_explicit_flag():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        timed_out_rollout = sessions / f"rollout-2026-01-01T00-00-00-{OPEN_SUBAGENT_SESSION_ID}.jsonl"
        recent_rollout = sessions / f"rollout-2026-01-01T00-00-01-{CHILD_SUBAGENT_SESSION_ID}.jsonl"
        write_subagent_jsonl(timed_out_rollout, OPEN_SUBAGENT_SESSION_ID)
        write_subagent_jsonl(recent_rollout, CHILD_SUBAGENT_SESSION_ID)

        state_db = home / "state_6.sqlite"
        conn = sqlite3.connect(str(state_db))
        try:
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT, updated_at_ms INTEGER, recency_at_ms INTEGER)"
            )
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
            parent_ms = 1_800_000_000_000
            timed_out_child_ms = parent_ms - 13 * 60 * 60 * 1000
            recent_child_ms = parent_ms - 2 * 60 * 60 * 1000
            conn.execute(
                "INSERT INTO threads (id, title, thread_source, source, updated_at_ms, recency_at_ms) VALUES (?, ?, ?, ?, ?, ?)",
                (FALLBACK_SESSION_ID, "Parent Work", "user", "vscode", parent_ms, parent_ms),
            )
            for sid, nickname, last_ms in (
                (OPEN_SUBAGENT_SESSION_ID, "TimedOut", timed_out_child_ms),
                (CHILD_SUBAGENT_SESSION_ID, "Recent", recent_child_ms),
            ):
                conn.execute(
                    "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source, updated_at_ms, recency_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid,
                        "Child prompt",
                        nickname,
                        "worker",
                        "subagent",
                        '{"subagent":{"thread_spawn":{"agent_nickname":"%s"}}}' % nickname,
                        last_ms,
                        last_ms,
                    ),
                )
                conn.execute(
                    "INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)",
                    (FALLBACK_SESSION_ID, sid, "open"),
                )
            conn.commit()
        finally:
            conn.close()

        refused = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_session_by_id.py"),
                OPEN_SUBAGENT_SESSION_ID,
                "--codex-home",
                str(home),
                "--json",
            ]
        )
        assert refused.returncode == 1
        refused_plan = json.loads(refused.stdout)["plan"]
        assert refused_plan["refused_not_closed"] == [OPEN_SUBAGENT_SESSION_ID]
        assert refused_plan["sessions"][OPEN_SUBAGENT_SESSION_ID]["is_timeout_subagent"] is True

        allowed = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_session_by_id.py"),
                OPEN_SUBAGENT_SESSION_ID,
                "--codex-home",
                str(home),
                "--allow-timeout-subagent",
                "--json",
            ]
        )
        assert allowed.returncode == 0, allowed.stderr + allowed.stdout
        allowed_plan = json.loads(allowed.stdout)["plan"]
        timeout_info = allowed_plan["sessions"][OPEN_SUBAGENT_SESSION_ID]["timeout_subagent"]
        assert allowed_plan["refused"] == []
        assert timeout_info["parent_thread_id"] == FALLBACK_SESSION_ID
        assert timeout_info["parent_title"] == "Parent Work"
        assert timeout_info["timeout_delta_hours"] == 13.0

        recent_refused = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_session_by_id.py"),
                CHILD_SUBAGENT_SESSION_ID,
                "--codex-home",
                str(home),
                "--allow-timeout-subagent",
                "--json",
            ]
        )
        assert recent_refused.returncode == 1
        recent_plan = json.loads(recent_refused.stdout)["plan"]
        assert recent_plan["sessions"][CHILD_SUBAGENT_SESSION_ID]["is_timeout_subagent"] is False
        assert recent_plan["refused_not_closed"] == [CHILD_SUBAGENT_SESSION_ID]


def test_cleanup_apply_refuses_running_codex_by_default():
    cleanup = load_cleanup_module()
    original = cleanup.running_codex_processes
    cleanup.running_codex_processes = lambda *_args, **_kwargs: [{"pid": "123", "command": "Codex", "args": "/Applications/Codex.app/Contents/MacOS/Codex"}]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            plan = {
                "codex_home": str(Path(tmp) / "codex-home"),
                "session_ids": [SUBAGENT_SESSION_ID],
                "refused": [],
                "rollout_files": [],
                "session_index": {"path": str(Path(tmp) / "session_index.jsonl"), "matching_lines": 0},
                "sqlite_counts": {},
            }
            args = SimpleNamespace(apply=True, yes=True, allow_running_codex=False)
            try:
                cleanup.apply_cleanup(args, plan)
            except RuntimeError as exc:
                assert "Codex appears to be running" in str(exc)
            else:
                raise AssertionError("apply_cleanup should refuse while Codex is running")
    finally:
        cleanup.running_codex_processes = original


def test_cleanup_running_codex_process_filter_is_target_home_scoped():
    cleanup = load_cleanup_module()
    with tempfile.TemporaryDirectory() as tmp:
        target_home = (Path(tmp) / "target-home").resolve()
        other_home = (Path(tmp) / "other-home").resolve()
        backup_root = (Path(tmp) / "backup-root").resolve()
        backup_path = cleanup.backup_path_for(target_home / "state_6.sqlite", backup_root)
        assert str(backup_path).startswith(str(backup_root / "files"))
        assert backup_path != target_home / "state_6.sqlite"
        assert cleanup.process_targets_codex_home("/Applications/Codex.app/Contents/MacOS/Codex", target_home) is False
        assert cleanup.process_targets_codex_home("codex app-server --stdio", target_home, f"CODEX_HOME={target_home}\0") is True
        assert cleanup.process_targets_codex_home("codex app-server --stdio", target_home, f"CODEX_HOME={other_home}\0") is False
        assert cleanup.process_targets_codex_home(f"CODEX_HOME={target_home} codex app-server --stdio", target_home) is True
        assert cleanup.process_targets_codex_home(f"CODEX_HOME={other_home} codex app-server --stdio", target_home) is False
        assert cleanup.process_targets_codex_home(f"codex --codex-home {target_home} app-server --stdio", target_home) is True
        assert cleanup.process_targets_codex_home("/Applications/Codex.app/Contents/MacOS/Codex", cleanup.fallback_codex_home()) is True
        assert cleanup.sqlite_uri(PureWindowsPath("C:/Users/Test User/state_6.sqlite")) == "file:C:/Users/Test%20User/state_6.sqlite?mode=ro"


def test_confirm_read_response_uses_queue_reader_path():
    confirm = load_confirm_module()
    stdout_queue = confirm.queue.Queue()
    stdout_queue.put(json.dumps({"jsonrpc": "2.0", "method": "thread/list", "params": {}}) + "\n")
    stdout_queue.put(json.dumps({"jsonrpc": "2.0", "id": "request-1", "result": {"ok": True}}) + "\n")
    proc = SimpleNamespace(poll=lambda: None)

    response, notifications = confirm.read_response(stdout_queue, proc, "request-1", 1)
    assert response["result"]["ok"] is True
    assert notifications == [{"method": "thread/list"}]


def test_confirm_requires_synthetic_user_and_agent_in_same_turn():
    confirm = load_confirm_module()
    split_turns = {
        "turns": [
            {"items": [{"type": "userMessage", "clientId": "codex-session-compress-elision-1", "text": "marker"}]},
            {"items": [{"type": "agentMessage", "text": "codex-session-compress marker"}]},
        ]
    }
    split_analysis = confirm.analyze_thread(split_turns)
    assert split_analysis["synthetic_user_found"] is True
    assert split_analysis["synthetic_agent_found"] is True
    assert split_analysis["synthetic_turn_found"] is False

    same_turn = {
        "turns": [
            {
                "items": [
                    {"type": "userMessage", "clientId": "codex-session-compress-elision-1", "text": "marker"},
                    {"type": "agentMessage", "text": "codex-session-compress marker"},
                ]
            }
        ]
    }
    same_analysis = confirm.analyze_thread(same_turn)
    assert same_analysis["synthetic_turn_found"] is True
    assert same_analysis["synthetic_turn_indexes"] == [0]


def test_cleanup_can_apply_without_cleanup_backup():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        sub_rollout = sessions / f"rollout-2026-01-01T00-00-03-{SUBAGENT_SESSION_ID}.jsonl"
        write_subagent_jsonl(sub_rollout)

        with (home / "session_index.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": SUBAGENT_SESSION_ID, "thread_name": "Kepler"}) + "\n")

        state_db = home / "state_6.sqlite"
        conn = sqlite3.connect(str(state_db))
        try:
            conn.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT)"
            )
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SUBAGENT_SESSION_ID,
                    "Long child prompt",
                    "Kepler",
                    "inspect one subsystem",
                    "subagent",
                    '{"subagent":{"thread_spawn":{"agent_nickname":"Kepler"}}}',
                ),
            )
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", ("parent", SUBAGENT_SESSION_ID, "closed"))
            conn.commit()
        finally:
            conn.close()

        applied = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_session_by_id.py"),
                SUBAGENT_SESSION_ID,
                "--codex-home",
                str(home),
                "--apply",
                "--yes",
                "--allow-running-codex",
                "--no-cleanup-backup",
                "--json",
            ]
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout
        manifest = json.loads(applied.stdout)["manifest"]
        assert manifest["cleanup_backup_enabled"] is False
        assert manifest["manifest_path"] == ""
        assert manifest["backups"] == []
        assert "not restore" in manifest["warning"] or "cannot restore" in manifest["warning"]
        assert not (home / "backups").exists()
        assert not sub_rollout.exists()
        assert (home / "session_index.jsonl").read_text(encoding="utf-8") == ""
        assert [item["action"] for item in manifest["actions"]] == [
            "update_state_sqlite_rows",
            "rewrite_session_index",
            "delete_file",
        ]

        conn = sqlite3.connect(str(state_db))
        try:
            assert conn.execute("SELECT COUNT(*) FROM threads WHERE id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_spawn_edges WHERE child_thread_id = ?", (SUBAGENT_SESSION_ID,)).fetchone()[0] == 0
        finally:
            conn.close()


def test_cleanup_cancels_agent_jobs_when_runner_and_worker_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "codex-home"
        sessions = home / "sessions" / "2026" / "01" / "01"
        sessions.mkdir(parents=True)
        root_rollout = sessions / f"rollout-2026-01-01T00-00-03-{SUBAGENT_SESSION_ID}.jsonl"
        child_rollout = sessions / f"rollout-2026-01-01T00-00-04-{CHILD_SUBAGENT_SESSION_ID}.jsonl"
        write_subagent_jsonl(root_rollout)
        write_subagent_jsonl(child_rollout, CHILD_SUBAGENT_SESSION_ID)

        state_db = home / "state_6.sqlite"
        conn = sqlite3.connect(str(state_db))
        try:
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, agent_nickname TEXT, agent_role TEXT, thread_source TEXT, source TEXT)")
            conn.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
            conn.execute("CREATE TABLE thread_dynamic_tools (thread_id TEXT, tool TEXT)")
            conn.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, status TEXT, updated_at INTEGER, completed_at INTEGER, last_error TEXT)")
            conn.execute("CREATE TABLE agent_job_items (job_id TEXT, item_id TEXT, status TEXT, assigned_thread_id TEXT, updated_at INTEGER, last_error TEXT)")
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (SUBAGENT_SESSION_ID, "Root", "Root", "runner", "subagent", '{"subagent":{}}'),
            )
            conn.execute(
                "INSERT INTO threads (id, title, agent_nickname, agent_role, thread_source, source) VALUES (?, ?, ?, ?, ?, ?)",
                (CHILD_SUBAGENT_SESSION_ID, "Child", "Child", "worker", "subagent", '{"subagent":{}}'),
            )
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", ("parent", SUBAGENT_SESSION_ID, "closed"))
            conn.execute("INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status) VALUES (?, ?, ?)", (SUBAGENT_SESSION_ID, CHILD_SUBAGENT_SESSION_ID, "closed"))
            conn.execute("INSERT INTO agent_jobs (id, status, updated_at, completed_at, last_error) VALUES (?, ?, ?, ?, ?)", ("job-1", "running", 0, None, None))
            conn.execute("INSERT INTO agent_job_items (job_id, item_id, status, assigned_thread_id, updated_at, last_error) VALUES (?, ?, ?, ?, ?, ?)", ("job-1", "item-1", "running", CHILD_SUBAGENT_SESSION_ID, 0, None))
            conn.commit()
        finally:
            conn.close()

        dry = run([sys.executable, str(SCRIPTS / "cleanup_session_by_id.py"), SUBAGENT_SESSION_ID, "--codex-home", str(home), "--json"])
        assert dry.returncode == 0, dry.stderr + dry.stdout
        plan = json.loads(dry.stdout)["plan"]
        assert plan["sqlite_counts"][str(state_db.resolve())]["agent_jobs_cancel"] == 1

        applied = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_session_by_id.py"),
                SUBAGENT_SESSION_ID,
                "--codex-home",
                str(home),
                "--apply",
                "--yes",
                "--allow-running-codex",
                "--json",
            ]
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout

        conn = sqlite3.connect(str(state_db))
        try:
            job = conn.execute("SELECT status, completed_at, last_error FROM agent_jobs WHERE id = ?", ("job-1",)).fetchone()
            assert job[0] == "cancelled"
            assert job[1] is not None
            assert job[2] == "agent job runner thread was deleted"
            item = conn.execute("SELECT status, assigned_thread_id, last_error FROM agent_job_items WHERE job_id = ?", ("job-1",)).fetchone()
            assert item[0] == "pending"
            assert item[1] is None
            assert item[2] == "assigned thread was deleted"
        finally:
            conn.close()


def test_cleanup_failure_removes_new_sqlite_sidecars():
    cleanup = load_cleanup_module()
    original_running = cleanup.running_codex_processes
    original_apply_state = cleanup.apply_state_db_changes
    cleanup.running_codex_processes = lambda *_args, **_kwargs: []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        backup_root = tmp_path / "backups"
        db_path = codex_home / "state_6.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO threads (id) VALUES (?)", (SUBAGENT_SESSION_ID,))
        conn.commit()
        conn.close()

        def failing_apply_state(db_path_arg, ids, vacuum):
            Path(str(db_path_arg) + "-wal").write_text("new wal\n", encoding="utf-8")
            Path(str(db_path_arg) + "-shm").write_text("new shm\n", encoding="utf-8")
            raise RuntimeError("forced sqlite failure")

        cleanup.apply_state_db_changes = failing_apply_state
        plan = {
            "codex_home": str(codex_home),
            "session_ids": [SUBAGENT_SESSION_ID],
            "refused": [],
            "rollout_files": [],
            "session_index": {"path": str(codex_home / "session_index.jsonl"), "matching_lines": 0},
            "sqlite_counts": {str(db_path): {"threads": 1}},
        }
        args = SimpleNamespace(
            apply=True,
            yes=True,
            allow_running_codex=False,
            backup_root=str(backup_root),
            vacuum=False,
        )
        try:
            cleanup.apply_cleanup(args, plan)
        except RuntimeError as exc:
            assert "cleanup failed and attempted restore" in str(exc)
        else:
            raise AssertionError("apply_cleanup should fail")
        finally:
            cleanup.apply_state_db_changes = original_apply_state
            cleanup.running_codex_processes = original_running

        assert not Path(str(db_path) + "-wal").exists()
        assert not Path(str(db_path) + "-shm").exists()
        manifests = list(backup_root.glob("session-cleanup-*/cleanup-manifest.json"))
        assert manifests
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["removed_new_sqlite_sidecars"]


def test_cleanup_compression_backups_requires_apply_yes_and_deletes_target_backups():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "sessions"
        day = base / "2026" / "01" / "01"
        day.mkdir(parents=True)
        target = day / f"rollout-2026-01-01T00-00-00-{SESSION_ID}.jsonl.orig"
        target.write_text("backup\n", encoding="utf-8")
        other_id = "123e4567-e89b-12d3-a456-426614174000"
        other = day / f"rollout-2026-01-01T00-00-01-{other_id}.jsonl.orig"
        other.write_text("other\n", encoding="utf-8")

        dry = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_compression_backups.py"),
                SESSION_ID,
                "--base",
                str(base),
                "--json",
            ]
        )
        assert dry.returncode == 0, dry.stderr + dry.stdout
        dry_data = json.loads(dry.stdout)
        assert dry_data["backup_count"] == 1
        assert dry_data["dry_run"] is True
        assert target.exists()
        assert other.exists()

        refused = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_compression_backups.py"),
                SESSION_ID,
                "--base",
                str(base),
                "--apply",
            ]
        )
        assert refused.returncode == 2
        assert target.exists()

        applied = run(
            [
                sys.executable,
                str(SCRIPTS / "cleanup_compression_backups.py"),
                SESSION_ID,
                "--base",
                str(base),
                "--apply",
                "--yes",
                "--json",
            ]
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout
        applied_data = json.loads(applied.stdout)
        assert applied_data["deleted_count"] == 1
        assert not target.exists()
        assert other.exists()


if __name__ == "__main__":
    test_repair_and_verify_synthetic_rollout()
    test_verify_can_require_compacted()
    test_semantic_verify_requires_full_compacted()
    test_semantic_verify_reports_mismatched_active_call_ids_as_normalized()
    test_semantic_verify_rejects_synthetic_user_marker_after_full_checkpoint()
    test_semantic_verify_rejects_malformed_synthetic_maintenance_turn()
    test_semantic_verify_allows_full_checkpoint_images()
    test_breadcrumb_preserves_original_response_items_within_goal()
    test_breadcrumb_preserves_app_visible_event_and_turn_context_items_first()
    test_physical_precheckpoint_omission_gets_placeholder_even_without_breadcrumb_candidates()
    test_repair_omits_existing_precheckpoint_compression_marker_events()
    test_list_rollouts_can_show_app_titles()
    test_list_rollouts_explicit_base_controls_title_home_over_env_codex_home()
    test_repair_auto_json_is_machine_readable_without_banner_text()
    test_compress_session_verify_only_json()
    test_compress_session_reports_inferred_codex_home_for_base()
    test_cleanup_session_by_id_removes_subagent_artifacts_only_after_apply()
    test_cleanup_allows_timed_out_open_subagent_only_with_explicit_flag()
    test_cleanup_apply_refuses_running_codex_by_default()
    test_cleanup_running_codex_process_filter_is_target_home_scoped()
    test_confirm_read_response_uses_queue_reader_path()
    test_confirm_requires_synthetic_user_and_agent_in_same_turn()
    test_cleanup_can_apply_without_cleanup_backup()
    test_cleanup_cancels_agent_jobs_when_runner_and_worker_deleted()
    test_cleanup_failure_removes_new_sqlite_sidecars()
    test_cleanup_compression_backups_requires_apply_yes_and_deletes_target_backups()
    print("synthetic tests passed")
