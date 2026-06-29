#!/usr/bin/env python3
"""
compress_session_by_id.py - compress a Codex session rollout by session ID.

Given a Codex session UUID, this script locates the matching rollout JSONL under
the Codex sessions directory, runs repair_rollout.py, then runs verify_rollout.py.
The repair step always archives the original by rename before replacement.

Backups are intentionally preserved during compression. Use
cleanup_compression_backups.py after explicit user confirmation to remove them.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?|bytes?)?\s*$", re.IGNORECASE)
DEFAULT_GOAL_SIZE = "100MB"


def is_uuid(value: str) -> bool:
    return bool(UUID_RE.match(value))


def parse_size(value: str) -> int:
    """Parse a human size. Plain numbers are interpreted as MiB."""
    text = str(value).strip()
    match = SIZE_RE.match(text)
    if not match:
        raise argparse.ArgumentTypeError("invalid size %r; use values like 100MB, 1.5GB, or 800MiB" % value)
    number = float(match.group(1))
    unit = (match.group(2) or "mb").lower().replace("ib", "b")
    factors = {
        "": 1024 * 1024,
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "k": 1024,
        "kb": 1024,
        "m": 1024 * 1024,
        "mb": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
        "t": 1024 * 1024 * 1024 * 1024,
        "tb": 1024 * 1024 * 1024 * 1024,
    }
    if unit not in factors:
        raise argparse.ArgumentTypeError("invalid size unit in %r" % value)
    return int(number * factors[unit])


def default_sessions_base() -> str:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return str(Path(os.path.expanduser(codex_home)) / "sessions")
    return "~/.codex/sessions"


def infer_codex_home_from_base(base: Path, explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()
    if base.name in ("sessions", "archived_sessions"):
        return base.parent.resolve()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(os.path.expanduser(codex_home)).resolve()
    return Path("~/.codex").expanduser().resolve()


def scan_bases(base: Path, include_archived: bool) -> List[Path]:
    bases = [base]
    if include_archived:
        archived = base.parent / "archived_sessions" if base.name == "sessions" else base / "archived_sessions"
        if archived not in bases:
            bases.append(archived)
    return bases


def rollout_paths(base: Path) -> List[Path]:
    if not base.exists():
        return []
    paths: List[Path] = []
    for root, _, files in os.walk(base):
        for filename in files:
            if filename.startswith("rollout-") and filename.endswith(".jsonl"):
                paths.append(Path(root) / filename)
    return paths


def rollout_paths_many(bases: Sequence[Path]) -> List[Path]:
    paths: List[Path] = []
    seen = set()
    for base in bases:
        for path in rollout_paths(base):
            key = str(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return paths


def session_meta_id(path: Path) -> Optional[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except Exception:
                    continue
                if obj.get("type") == "session_meta":
                    payload = obj.get("payload") or {}
                    sid = payload.get("id")
                    return sid if isinstance(sid, str) else None
                break
    except OSError:
        return None
    return None


def find_rollouts_by_id(session_id: str, bases: Sequence[Path], scan_meta: bool) -> List[Path]:
    """Return rollout paths that match a session ID.

    Fast path: filename suffix. Fallback: session_meta.id scan when requested or
    when filename matching finds nothing.
    """
    paths = rollout_paths_many(bases)
    suffix = f"{session_id}.jsonl"
    matches = [path for path in paths if path.name.endswith(suffix)]
    if matches and not scan_meta:
        return matches
    if scan_meta or not matches:
        for path in paths:
            if path in matches:
                continue
            if session_meta_id(path) == session_id:
                matches.append(path)
    return matches


def choose_rollout(matches: Sequence[Path]) -> Optional[Path]:
    """Pick the largest/newest matching rollout."""
    if not matches:
        return None

    def sort_key(path: Path) -> Tuple[int, float]:
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime
        except OSError:
            return 0, 0.0

    return sorted(matches, key=sort_key, reverse=True)[0]


def backup_candidates(rollout_path: Path, backup_dir: Optional[str]) -> List[Path]:
    directory = Path(os.path.expanduser(backup_dir)) if backup_dir else rollout_path.parent
    prefix = rollout_path.name + ".orig"
    if not directory.exists():
        return []
    candidates = [p for p in directory.iterdir() if p.name == prefix or p.name.startswith(prefix + ".")]
    return sorted(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def backups_for_session(session_id: str, bases: Sequence[Path], backup_dir: Optional[str], scan_meta: bool) -> List[Path]:
    matches = find_rollouts_by_id(session_id, bases, scan_meta)
    seen = set()
    backups: List[Path] = []
    for rollout in matches:
        for backup in backup_candidates(rollout, backup_dir):
            key = str(backup)
            if key not in seen:
                seen.add(key)
                backups.append(backup)
    return sorted(backups, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def newest_backup(rollout_path: Path, backup_dir: Optional[str], before: Sequence[Path]) -> Optional[Path]:
    before_set = set(str(path) for path in before)
    after = backup_candidates(rollout_path, backup_dir)
    new_files = [path for path in after if str(path) not in before_set]
    if not new_files:
        return None
    return sorted(new_files, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def quote_cmd(cmd: Sequence[str]) -> str:
    out = []
    for part in cmd:
        if not part or any(ch.isspace() for ch in part):
            out.append("'%s'" % part.replace("'", "'\\''"))
        else:
            out.append(part)
    return " ".join(out)


def run_command(cmd: Sequence[str]) -> int:
    print("$ " + quote_cmd(cmd), flush=True)
    return subprocess.run(list(cmd)).returncode


def run_json_command(cmd: Sequence[str]) -> Tuple[int, Optional[dict], str, str]:
    proc = subprocess.run(list(cmd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data = None
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
    return proc.returncode, data, proc.stdout, proc.stderr


def run_repair(
    rollout_path: Path,
    goal_size: str,
    dry_run: bool,
    backup_dir: Optional[str],
    json_mode: bool = False,
) -> int:
    script = Path(__file__).with_name("repair_rollout.py")
    cmd = [sys.executable, str(script), str(rollout_path), "--goal-size", goal_size]
    if dry_run:
        cmd.append("--dry-run")
    if backup_dir:
        cmd.extend(["--backup-dir", os.path.expanduser(backup_dir)])
    if json_mode:
        cmd.append("--json")
    return run_command(cmd)


def run_repair_json(
    rollout_path: Path,
    goal_size: str,
    dry_run: bool,
    backup_dir: Optional[str],
) -> Tuple[int, Optional[dict], str, str]:
    script = Path(__file__).with_name("repair_rollout.py")
    cmd = [sys.executable, str(script), str(rollout_path), "--goal-size", goal_size, "--json"]
    if dry_run:
        cmd.append("--dry-run")
    if backup_dir:
        cmd.extend(["--backup-dir", os.path.expanduser(backup_dir)])
    return run_json_command(cmd)


def run_verify(rollout_path: Path, require_compacted: bool, require_full_compacted: bool, semantic_checkpoint: bool) -> int:
    script = Path(__file__).with_name("verify_rollout.py")
    cmd = [sys.executable, str(script), str(rollout_path)]
    if require_compacted:
        cmd.append("--require-compacted")
    if require_full_compacted:
        cmd.append("--require-full-compacted")
    if semantic_checkpoint:
        cmd.extend(["--semantic-checkpoint", "--allow-active-images"])
    return run_command(cmd)


def run_verify_json(rollout_path: Path, require_compacted: bool, require_full_compacted: bool, semantic_checkpoint: bool) -> Tuple[int, Optional[dict], str, str]:
    script = Path(__file__).with_name("verify_rollout.py")
    cmd = [sys.executable, str(script), str(rollout_path), "--json"]
    if require_compacted:
        cmd.append("--require-compacted")
    if require_full_compacted:
        cmd.append("--require-full-compacted")
    if semantic_checkpoint:
        cmd.extend(["--semantic-checkpoint", "--allow-active-images"])
    return run_json_command(cmd)


def run_codex_cli_confirm(session_id: str, require_synthetic_marker: bool, timeout: float, codex_home: Optional[Path]) -> int:
    script = Path(__file__).with_name("confirm_session_with_codex_cli.py")
    cmd = [sys.executable, str(script), session_id, "--timeout", str(timeout)]
    if codex_home:
        cmd.extend(["--codex-home", str(codex_home)])
    if require_synthetic_marker:
        cmd.append("--require-synthetic-marker")
    return run_command(cmd)


def run_codex_cli_confirm_json(session_id: str, require_synthetic_marker: bool, timeout: float, codex_home: Optional[Path]) -> Tuple[int, Optional[dict], str, str]:
    script = Path(__file__).with_name("confirm_session_with_codex_cli.py")
    cmd = [sys.executable, str(script), session_id, "--timeout", str(timeout), "--json"]
    if codex_home:
        cmd.extend(["--codex-home", str(codex_home)])
    if require_synthetic_marker:
        cmd.append("--require-synthetic-marker")
    return run_json_command(cmd)


def rollout_has_synthetic_marker(path: Path) -> bool:
    needle = b"codex-session-compress-elision-"
    overlap = len(needle) - 1
    previous = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                data = previous + chunk
                if needle in data:
                    return True
                previous = data[-overlap:] if overlap > 0 else b""
    except OSError:
        return False
    return False


def restore_backup(backup_path: Optional[Path], rollout_path: Path) -> int:
    if not backup_path or not backup_path.exists():
        print("No backup path was detected; cannot automatically restore.", file=sys.stderr)
        return 1
    print("Verification failed; moving backup back into place:")
    print(f"  backup : {backup_path}")
    print(f"  target : {rollout_path}")
    try:
        os.replace(str(backup_path), str(rollout_path))
        return 0
    except OSError as exc:
        print(f"Failed to restore backup by rename: {exc}", file=sys.stderr)
        return 1


def restore_backup_silent(backup_path: Optional[Path], rollout_path: Path) -> int:
    if not backup_path or not backup_path.exists():
        return 1
    try:
        os.replace(str(backup_path), str(rollout_path))
    except OSError:
        return 1
    return 0


def run_codex_resume(session_id: str, codex_home: Optional[Path]) -> int:
    codex = shutil.which("codex")
    if not codex:
        print("codex CLI not found on PATH. Skipping Codex CLI reload check.", file=sys.stderr)
        return 127
    print("Launching interactive Codex CLI reload check. Exit Codex when finished.")
    env = os.environ.copy()
    if codex_home:
        env["CODEX_HOME"] = str(codex_home)
    print("$ " + quote_cmd([codex, "resume", session_id]), flush=True)
    return subprocess.run([codex, "resume", session_id], env=env).returncode


def print_backups(backups: Sequence[Path]) -> None:
    if not backups:
        print("No backups found for this session.")
        return
    print("Backups for this session:")
    for index, path in enumerate(backups, start=1):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        print(f"{index:2d}. {size:>12} bytes  {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compress a Codex session rollout by session ID, then verify safety.")
    parser.add_argument("session_id", help="Codex session UUID")
    parser.add_argument("--base", default=default_sessions_base(), help="Base sessions directory. Default: $CODEX_HOME/sessions if CODEX_HOME is set, otherwise ~/.codex/sessions.")
    parser.add_argument("--codex-home", default=None, help="Codex home for Codex CLI/app-server confirmation. Default: inferred from --base when it is a sessions or archived_sessions directory.")
    parser.add_argument("--goal-size", default=DEFAULT_GOAL_SIZE, help="Target rollout size. Default: 100MB. Plain numbers mean MiB.")
    parser.add_argument("--dry-run", action="store_true", help="Preview repair operations without modifying files.")
    parser.add_argument("--backup-dir", default=None, help="Directory for rename backups. Default: rollout directory; must be on the same filesystem.")
    parser.add_argument("--scan-meta", action="store_true", help="Scan session_meta.id in rollout files in addition to filename matching.")
    parser.add_argument("--include-archived", action="store_true", help="Also search archived_sessions next to the selected sessions base.")
    parser.add_argument("--list-backups", action="store_true", help="List backups for this session and exit.")
    parser.add_argument("--verify-only", action="store_true", help="Only run strict semantic verification for the selected rollout; do not repair.")
    parser.add_argument("--force-semantic-verify", action="store_true", help="Run semantic checkpoint verification even when the file was already under --goal-size.")
    parser.add_argument("--skip-codex-cli-confirm", action="store_true", help="Skip the default post-compression `codex app-server thread/read` confirmation.")
    parser.add_argument("--codex-cli-confirm-timeout", type=float, default=120.0, help="Seconds to wait for each Codex CLI app-server confirmation response. Default: 120.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable wrapper result JSON.")
    parser.add_argument(
        "--codex-check",
        choices=("none", "resume"),
        default="none",
        help="Optional post-verify Codex CLI check. 'resume' runs interactive `codex resume <session_id>`. Default: none.",
    )
    parser.add_argument("--no-auto-restore", action="store_true", help="If verification fails, do not automatically restore the detected backup.")
    args = parser.parse_args()

    session_id = args.session_id.strip()
    base = Path(os.path.expanduser(args.base))
    codex_home = infer_codex_home_from_base(base, args.codex_home)
    bases = scan_bases(base, args.include_archived)

    if not is_uuid(session_id):
        print("warning: session_id does not look like a full UUID; matching by filename suffix and metadata anyway.", file=sys.stderr)

    if args.list_backups:
        backups = backups_for_session(session_id, bases, args.backup_dir, args.scan_meta)
        if args.json:
            print(json.dumps({"session_id": session_id, "backups": [str(path) for path in backups]}, ensure_ascii=False, indent=2))
        else:
            print_backups(backups)
        return 0

    matches = find_rollouts_by_id(session_id, bases, args.scan_meta)
    if not matches:
        searched = ", ".join(str(path) for path in bases)
        print(f"error: no rollout found for session id {session_id} under {searched}", file=sys.stderr)
        print("hint: run scripts/list_rollouts.py --top 0 --show-cwd, or retry with --scan-meta --include-archived", file=sys.stderr)
        return 1

    rollout_path = choose_rollout(matches)
    assert rollout_path is not None
    if not args.json:
        print(f"Selected rollout: {rollout_path}")
    if len(matches) > 1 and not args.json:
        print(f"Note: {len(matches)} matching rollouts found; selected the largest/newest one.")

    before_backups = backup_candidates(rollout_path, args.backup_dir)
    # Validate early so errors point to this wrapper, not only the delegated script.
    goal_bytes = parse_size(args.goal_size)
    try:
        repair_was_needed = rollout_path.stat().st_size > goal_bytes
    except OSError:
        repair_was_needed = True

    if args.verify_only:
        if args.json:
            verify_rc, verify_data, verify_stdout, verify_stderr = run_verify_json(
                rollout_path,
                require_compacted=True,
                require_full_compacted=True,
                semantic_checkpoint=True,
            )
            print(
                json.dumps(
                    {
                        "session_id": session_id,
                        "rollout_path": str(rollout_path),
                        "codex_home": str(codex_home),
                        "mode": "verify_only",
                        "verify_rc": verify_rc,
                        "verify": verify_data,
                        "stdout": verify_stdout if verify_data is None else "",
                        "stderr": verify_stderr,
                        "ok": verify_rc == 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return verify_rc
        print("\nVerifying rollout without repair...")
        return run_verify(
            rollout_path,
            require_compacted=True,
            require_full_compacted=True,
            semantic_checkpoint=True,
        )

    if args.json:
        repair_rc, repair_data, repair_stdout, repair_stderr = run_repair_json(
            rollout_path,
            args.goal_size,
            args.dry_run,
            args.backup_dir,
        )
        result = {
            "session_id": session_id,
            "rollout_path": str(rollout_path),
            "codex_home": str(codex_home),
            "matches": [str(path) for path in matches],
            "goal_size": args.goal_size,
            "dry_run": bool(args.dry_run),
            "repair_rc": repair_rc,
            "repair": repair_data,
            "repair_stdout": repair_stdout if repair_data is None else "",
            "repair_stderr": repair_stderr,
            "verify_rc": None,
            "verify": None,
            "codex_cli_confirm_rc": None,
            "codex_cli_confirm": None,
            "restored": False,
            "ok": False,
        }
        if repair_rc != 0 or args.dry_run:
            result["ok"] = repair_rc == 0
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return repair_rc
        backup_path = Path(repair_data["backup_path"]) if repair_data and repair_data.get("backup_path") else newest_backup(rollout_path, args.backup_dir, before_backups)
        semantic_verify = args.force_semantic_verify or repair_was_needed
        verify_rc, verify_data, verify_stdout, verify_stderr = run_verify_json(
            rollout_path,
            require_compacted=semantic_verify,
            require_full_compacted=semantic_verify,
            semantic_checkpoint=semantic_verify,
        )
        result.update(
            {
                "backup_path": str(backup_path) if backup_path else "",
                "semantic_verify": semantic_verify,
                "verify_rc": verify_rc,
                "verify": verify_data,
                "verify_stdout": verify_stdout if verify_data is None else "",
                "verify_stderr": verify_stderr,
                "ok": verify_rc == 0,
            }
        )
        if verify_rc != 0 and not args.no_auto_restore:
            restore_rc = restore_backup_silent(backup_path, rollout_path)
            result["restore_rc"] = restore_rc
            result["restored"] = restore_rc == 0
        if verify_rc == 0 and not args.skip_codex_cli_confirm and repair_was_needed:
            require_synthetic_marker = bool(
                (repair_data or {}).get("placeholder_line_count", 0) or rollout_has_synthetic_marker(rollout_path)
            )
            confirm_rc, confirm_data, confirm_stdout, confirm_stderr = run_codex_cli_confirm_json(
                session_id,
                require_synthetic_marker=require_synthetic_marker,
                timeout=args.codex_cli_confirm_timeout,
                codex_home=codex_home,
            )
            result.update(
                {
                    "codex_cli_confirm_required": True,
                    "codex_cli_confirm_require_synthetic_marker": require_synthetic_marker,
                    "codex_cli_confirm_rc": confirm_rc,
                    "codex_cli_confirm": confirm_data,
                    "codex_cli_confirm_stdout": confirm_stdout if confirm_data is None else "",
                    "codex_cli_confirm_stderr": confirm_stderr,
                    "ok": confirm_rc == 0,
                }
            )
            if confirm_rc != 0 and not args.no_auto_restore:
                restore_rc = restore_backup_silent(backup_path, rollout_path)
                result["restore_rc"] = restore_rc
                result["restored"] = restore_rc == 0
        else:
            result["codex_cli_confirm_required"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if verify_rc != 0:
            return verify_rc if result.get("restored") else result.get("restore_rc", verify_rc)
        if result.get("codex_cli_confirm_rc") not in (None, 0):
            return result["codex_cli_confirm_rc"] if result.get("restored") else result.get("restore_rc", result["codex_cli_confirm_rc"])
        return 0

    sys.stdout.flush()
    rc = run_repair(
        rollout_path,
        args.goal_size,
        args.dry_run,
        args.backup_dir,
    )
    if rc != 0:
        print("repair_rollout.py failed; aborting.", file=sys.stderr)
        return rc
    if args.dry_run:
        return 0

    backup_path = newest_backup(rollout_path, args.backup_dir, before_backups)
    if backup_path:
        print(f"Detected backup: {backup_path}")
    else:
        print("No new backup detected. This can be normal if the file was already under target or no operation changed it.")

    print("\nVerifying repaired rollout...")
    semantic_verify = args.force_semantic_verify or repair_was_needed
    verify_rc = run_verify(
        rollout_path,
        require_compacted=semantic_verify,
        require_full_compacted=semantic_verify,
        semantic_checkpoint=semantic_verify,
    )
    if verify_rc != 0:
        print("verify_rollout.py failed.", file=sys.stderr)
        if not args.no_auto_restore:
            restore_rc = restore_backup(backup_path, rollout_path)
            return verify_rc if restore_rc == 0 else restore_rc
        return verify_rc

    print("\nVerification succeeded: rollout is safe to load.")
    if not args.skip_codex_cli_confirm and repair_was_needed:
        require_synthetic_marker = rollout_has_synthetic_marker(rollout_path)
        print("\nConfirming repaired session through Codex CLI app-server thread/read...")
        confirm_rc = run_codex_cli_confirm(
            session_id,
            require_synthetic_marker=require_synthetic_marker,
            timeout=args.codex_cli_confirm_timeout,
            codex_home=codex_home,
        )
        if confirm_rc != 0:
            print("Codex CLI confirmation failed.", file=sys.stderr)
            if not args.no_auto_restore:
                restore_rc = restore_backup(backup_path, rollout_path)
                return confirm_rc if restore_rc == 0 else restore_rc
            return confirm_rc
        print("\nCodex CLI confirmation succeeded: thread/read can reconstruct the session%s." % (
            " and the synthetic compression marker is visible" if require_synthetic_marker else ""
        ))
    if args.codex_check == "resume":
        codex_rc = run_codex_resume(session_id, codex_home)
        if codex_rc != 0:
            print(f"Codex CLI check exited with code {codex_rc}.", file=sys.stderr)
            return codex_rc

    if backup_path:
        print("\nBackup preserved for rollback:")
        print(f"  {backup_path}")
        print("After the user confirms the repaired session opens correctly, remove it with:")
        print(f"  python {Path(__file__).with_name('cleanup_compression_backups.py')} {session_id} --apply --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
