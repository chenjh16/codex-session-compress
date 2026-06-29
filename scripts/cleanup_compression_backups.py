#!/usr/bin/env python3
"""
cleanup_compression_backups.py - dry-run or remove rollout compression backups.

This script only targets sidecar backups produced by repair_rollout.py:

  rollout-*.jsonl.orig
  rollout-*.jsonl.orig.N

It defaults to dry-run. Deletion requires both --apply and --yes.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

BACKUP_RE = re.compile(
    r"^rollout-.+-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl\.orig(?:\.\d+)?$"
)


def default_sessions_base() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(os.path.expanduser(codex_home)) / "sessions"
    return Path("~/.codex/sessions").expanduser()


def human(value: int) -> str:
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%dB" % int(n)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024
    return "%dB" % value


def print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def scan_bases(base: Path, include_archived: bool) -> List[Path]:
    bases = [base]
    if include_archived:
        archived = base.parent / "archived_sessions" if base.name == "sessions" else base / "archived_sessions"
        if archived not in bases:
            bases.append(archived)
    return bases


def backup_session_id(path: Path) -> Optional[str]:
    match = BACKUP_RE.match(path.name)
    return match.group(1).lower() if match else None


def find_backups(bases: Sequence[Path], session_ids: Sequence[str], all_backups: bool) -> List[Path]:
    wanted = {sid.lower() for sid in session_ids}
    found: Dict[str, Path] = {}
    for base in bases:
        base = base.expanduser().resolve()
        if not base.exists():
            continue
        for path in base.rglob("rollout-*.jsonl.orig*"):
            if not path.is_file():
                continue
            sid = backup_session_id(path)
            if not sid:
                continue
            if not all_backups and sid not in wanted:
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(base)
            except ValueError:
                continue
            found[str(resolved)] = resolved
    return sorted(found.values(), key=lambda p: str(p))


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def build_plan(backups: Sequence[Path]) -> Dict:
    entries = []
    total = 0
    for path in backups:
        size = file_size(path)
        total += size
        entries.append(
            {
                "path": str(path),
                "session_id": backup_session_id(path),
                "bytes": size,
                "size": human(size),
            }
        )
    return {
        "backup_count": len(entries),
        "bytes": total,
        "size": human(total),
        "backups": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or remove Codex rollout compression backups.")
    parser.add_argument("session_ids", nargs="*", help="Session IDs whose rollout .jsonl.orig backups should be cleaned.")
    parser.add_argument("--all", action="store_true", help="Clean every rollout-*.jsonl.orig* backup under the selected sessions base.")
    parser.add_argument("--base", default=str(default_sessions_base()), help="Base sessions directory. Default: $CODEX_HOME/sessions if CODEX_HOME is set, otherwise ~/.codex/sessions.")
    parser.add_argument("--include-archived", action="store_true", help="Also scan archived_sessions next to the selected sessions base.")
    parser.add_argument("--apply", action="store_true", help="Delete the planned backup files. Requires --yes.")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive deletion when used with --apply.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    if args.all and args.session_ids:
        print("error: use either --all or explicit session IDs, not both", file=sys.stderr)
        return 2
    if not args.all and not args.session_ids:
        print("error: provide one or more session IDs, or use --all", file=sys.stderr)
        return 2
    if args.apply and not args.yes:
        print("error: --apply requires --yes", file=sys.stderr)
        return 2

    base = Path(os.path.expanduser(args.base)).resolve()
    bases = scan_bases(base, args.include_archived)
    backups = find_backups(bases, args.session_ids, args.all)
    plan = build_plan(backups)
    result = {
        "base": str(base),
        "include_archived": bool(args.include_archived),
        "all": bool(args.all),
        "requested_session_ids": args.session_ids,
        "dry_run": not bool(args.apply),
        "deleted": [],
        "failed": [],
        **plan,
    }

    if args.apply:
        deleted = []
        failed = []
        for path in backups:
            entry = {
                "path": str(path),
                "session_id": backup_session_id(path),
                "bytes": file_size(path),
            }
            try:
                path.unlink()
                entry["deleted"] = True
                deleted.append(entry)
            except OSError as exc:
                entry["deleted"] = False
                entry["error"] = str(exc)
                failed.append(entry)
        result["deleted"] = deleted
        result["failed"] = failed
        result["deleted_count"] = len(deleted)
        result["deleted_bytes"] = sum(item["bytes"] for item in deleted)
        result["deleted_size"] = human(result["deleted_bytes"])
        result["ok"] = not failed
    else:
        result["ok"] = True

    if args.json:
        print_json(result)
    else:
        action = "Would delete" if not args.apply else "Deleted"
        size = result.get("deleted_size", result["size"]) if args.apply else result["size"]
        count = result.get("deleted_count", result["backup_count"]) if args.apply else result["backup_count"]
        print("%s %d compression backup file(s), total %s." % (action, count, size))
        for item in result["backups"]:
            print("  %10s  %s" % (item["size"], item["path"]))
        if not args.apply:
            print("\n[dry-run] Re-run with --apply --yes to delete these backups.")
        elif result["failed"]:
            print("\nFailed to delete %d file(s)." % len(result["failed"]), file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
