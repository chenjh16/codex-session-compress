#!/usr/bin/env python3
"""Restore files and SQLite DBs from a cleanup-manifest.json backup manifest.

Default mode is dry-run. Applying restore requires both --apply and --yes.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence
from urllib.parse import quote


def sqlite_uri(path: Path) -> str:
    return "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def fallback_codex_home() -> Path:
    return Path(os.path.expanduser("~/.codex")).resolve()


def extract_codex_home_from_env_text(text: str) -> str:
    for chunk in re.split(r"[\0\s]+", text or ""):
        if chunk.startswith("CODEX_HOME="):
            return chunk.split("=", 1)[1]
    return ""


def process_environment_text(pid: int) -> str:
    environ_path = Path("/proc") / str(pid) / "environ"
    try:
        return environ_path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        pass
    try:
        proc = subprocess.run(
            ["ps", "eww", "-p", str(pid), "-o", "command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=1,
        )
    except Exception:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def process_targets_codex_home(args: str, target_codex_home: Path, env_text: str = "") -> bool:
    target = target_codex_home.resolve()
    env_home = extract_codex_home_from_env_text(env_text)
    if env_home:
        return Path(os.path.expanduser(env_home)).resolve() == target
    expanded_args = os.path.expanduser(args or "")
    if str(target) in expanded_args:
        return True
    if "CODEX_HOME=" in expanded_args or "--codex-home" in expanded_args:
        return False
    return target == fallback_codex_home()


def manifest_codex_home(manifest: Dict[str, Any]) -> Path:
    plan = manifest.get("plan") if isinstance(manifest.get("plan"), dict) else {}
    value = plan.get("codex_home") if isinstance(plan, dict) else None
    if isinstance(value, str) and value.strip():
        return Path(os.path.expanduser(value)).resolve()
    return fallback_codex_home()


def running_codex_processes(target_codex_home: Path) -> List[Dict[str, str]]:
    current_pid = os.getpid()
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,comm=,args="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []

    matches: List[Dict[str, str]] = []
    for line in proc.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(None, 2)
        if len(parts) < 2:
            continue
        pid_text, command = parts[0], parts[1]
        args = parts[2] if len(parts) > 2 else command
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        command_name = Path(command).name
        if command_name == "Codex" or command_name == "codex" or "/Codex.app/" in args:
            env_text = process_environment_text(pid)
            if not process_targets_codex_home(args, target_codex_home, env_text):
                continue
            matches.append(
                {
                    "pid": str(pid),
                    "command": command_name,
                    "args": args,
                    "process_codex_home": extract_codex_home_from_env_text(env_text),
                    "target_codex_home": str(target_codex_home),
                }
            )
    return matches


def format_processes(processes: Sequence[Dict[str, str]]) -> str:
    return "; ".join(
        f"pid={item.get('pid')} command={item.get('command')} args={item.get('args')}"
        for item in processes
    )


def integrity_check(db_path: Path) -> str:
    try:
        conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
        try:
            return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:
        return f"ERROR: {exc}"


def load_manifest(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        obj = json.load(handle)
    if not isinstance(obj, dict):
        raise ValueError("manifest root is not an object")
    backups = obj.get("backups")
    if not isinstance(backups, list):
        raise ValueError("manifest does not contain a backups array")
    return obj


def restore_plan(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for item in reversed(manifest.get("backups", [])):
        if not isinstance(item, dict):
            continue
        source = Path(str(item.get("source") or ""))
        backup = Path(str(item.get("backup") or ""))
        actions.append(
            {
                "source": str(source),
                "backup": str(backup),
                "backup_exists": backup.exists(),
                "backup_bytes": file_size(backup),
                "source_exists": source.exists(),
            }
        )
    return actions


def apply_restore(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    restored: List[Dict[str, Any]] = []
    integrity: Dict[str, str] = {}
    for action in actions:
        source = Path(action["source"])
        backup = Path(action["backup"])
        if not backup.exists():
            restored.append({**action, "restored": False, "error": "backup missing"})
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(backup), str(source))
            restored.append({**action, "restored": True})
            if source.name.endswith(".sqlite"):
                integrity[str(source)] = integrity_check(source)
        except Exception as exc:
            restored.append({**action, "restored": False, "error": str(exc)})
    return {"restored": restored, "integrity": integrity}


def print_plan(manifest_path: Path, actions: List[Dict[str, Any]]) -> None:
    print("Codex cleanup manifest restore plan")
    print(f"Manifest: {manifest_path}")
    print("Files:")
    if not actions:
        print("  none")
        return
    for action in actions:
        status = "OK" if action["backup_exists"] else "MISSING"
        print(f"  - [{status}] {action['backup']} -> {action['source']} ({action['backup_bytes']} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a Codex session cleanup backup manifest.")
    parser.add_argument("manifest", help="Path to cleanup-manifest.json")
    parser.add_argument("--apply", action="store_true", help="Apply the restore. Without this, only a dry-run plan is printed.")
    parser.add_argument("--yes", action="store_true", help="Required together with --apply.")
    parser.add_argument("--allow-running-codex", action="store_true", help="Allow restore while Codex App or codex CLI appears to be using the target Codex home.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable plan/result JSON.")
    args = parser.parse_args()

    manifest_path = Path(os.path.expanduser(args.manifest)).resolve()
    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:
        print(f"error: failed to read manifest: {exc}", file=sys.stderr)
        return 2
    actions = restore_plan(manifest)

    if not args.apply:
        if args.json:
            print(json.dumps({"dry_run": True, "manifest": str(manifest_path), "actions": actions}, ensure_ascii=False, indent=2))
        else:
            print_plan(manifest_path, actions)
            print("\n[dry-run] no files restored. Re-run with --apply --yes to restore.")
        return 0

    if not args.yes:
        print("error: restore requires --apply --yes", file=sys.stderr)
        return 2
    target_codex_home = manifest_codex_home(manifest)
    codex_processes = running_codex_processes(target_codex_home)
    if codex_processes and not args.allow_running_codex:
        print(
            "error: Codex appears to be running for the target Codex home; close that Codex instance before restoring or pass --allow-running-codex. "
            + format_processes(codex_processes[:5]),
            file=sys.stderr,
        )
        return 1

    result = apply_restore(actions)
    failed = [item for item in result["restored"] if not item.get("restored")]
    integrity_failed = {path: value for path, value in result["integrity"].items() if value != "ok"}
    ok = not failed and not integrity_failed
    payload = {
        "dry_run": False,
        "manifest": str(manifest_path),
        "codex_processes": codex_processes,
        **result,
        "ok": ok,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Restore applied." if ok else "Restore completed with errors.")
        for item in result["restored"]:
            status = "restored" if item.get("restored") else f"FAILED: {item.get('error')}"
            print(f"  - {status}: {item['source']}")
        for db_path, check in result["integrity"].items():
            print(f"Integrity {db_path}: {check}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
