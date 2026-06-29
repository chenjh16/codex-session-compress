#!/usr/bin/env python3
"""
list_rollouts.py - list local Codex JSONL files by size.

Default behavior scans CODEX_HOME/sessions when CODEX_HOME is set, otherwise
~/.codex/sessions, for rollout-*.jsonl files. It sorts them by size in
descending order and prints the top 10. Use --top 0 for all.

This script is intentionally stdlib-only and streams only the first metadata
record from each rollout. Optional title lookup reads only Codex's small local
metadata files, so it remains safe on large session files.
"""

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)
STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")
SQLITE_HOME_RE = re.compile(r"^\s*sqlite_home\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$")
SUBAGENT_TITLE_PREFIX = "【Sub】"


def default_sessions_base() -> str:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return str(Path(os.path.expanduser(codex_home)) / "sessions")
    return "~/.codex/sessions"


def infer_codex_home(base: Path, explicit: Optional[str]) -> Path:
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()
    if base.name in ("sessions", "archived_sessions"):
        return base.parent.resolve()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(os.path.expanduser(codex_home)).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def config_sqlite_home(codex_home: Path) -> Optional[Path]:
    config_path = codex_home / "config.toml"
    try:
        with config_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = SQLITE_HOME_RE.match(line)
                if match:
                    value = os.path.expandvars(os.path.expanduser(match.group(2)))
                    path = Path(value)
                    return path if path.is_absolute() else (codex_home / path)
    except OSError:
        return None
    return None


def infer_sqlite_home(codex_home: Path, explicit: Optional[str]) -> Path:
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()
    configured = config_sqlite_home(codex_home)
    if configured:
        return configured.resolve()
    sqlite_home = os.environ.get("CODEX_SQLITE_HOME")
    if sqlite_home:
        return Path(os.path.expanduser(sqlite_home)).resolve()
    return codex_home


def human_bytes(value: int) -> str:
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{value}B"


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def truncate_text(value: str, max_chars: int) -> str:
    text = compact_text(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def session_id_from_filename(path: Path) -> Optional[str]:
    match = UUID_RE.search(path.name)
    return match.group(1) if match else None


def project_dir_name(cwd: Optional[str]) -> str:
    if not cwd:
        return ""
    text = cwd.rstrip("/").strip()
    if not text:
        return ""
    return Path(text).name


def source_agent_fields(source: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except Exception:
            return None, None
    if not isinstance(source, dict):
        return None, None
    subagent = source.get("subagent") or source.get("subAgent")
    if not isinstance(subagent, dict):
        return None, None
    spawn = subagent.get("thread_spawn") or subagent.get("threadSpawn")
    if not isinstance(spawn, dict):
        return None, None
    nickname = spawn.get("agent_nickname") or spawn.get("agentNickname")
    role = spawn.get("agent_role") or spawn.get("agentRole")
    return (
        nickname.strip() if isinstance(nickname, str) and nickname.strip() else None,
        role.strip() if isinstance(role, str) and role.strip() else None,
    )


def session_meta(path: Path) -> Tuple[Optional[str], Optional[str], bool, Optional[str], Optional[str]]:
    """Return light metadata from the first session_meta record, if present."""
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
                    sid = payload.get("id") if isinstance(payload.get("id"), str) else None
                    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
                    source = payload.get("source")
                    thread_source = payload.get("thread_source") or payload.get("threadSource")
                    is_subagent = source_is_subagent(source, thread_source)
                    source_nickname, source_role = source_agent_fields(source)
                    nickname = payload.get("agent_nickname") or payload.get("agentNickname") or source_nickname
                    role = payload.get("agent_role") or payload.get("agentRole") or source_role
                    return (
                        sid,
                        cwd,
                        is_subagent,
                        nickname.strip() if isinstance(nickname, str) and nickname.strip() else None,
                        role.strip() if isinstance(role, str) and role.strip() else None,
                    )
                # Rollouts normally start with session_meta; avoid deep scanning
                # multi-GB files if the first meaningful line is not metadata.
                break
    except OSError:
        return None, None, False, None, None
    return None, None, False, None, None


def should_include(filename: str, all_jsonl: bool) -> bool:
    if all_jsonl:
        return filename.endswith(".jsonl")
    return filename.startswith("rollout-") and filename.endswith(".jsonl")


def iter_jsonl_files(base: Path, all_jsonl: bool) -> Iterator[Dict[str, Any]]:
    for root, _, files in os.walk(base):
        for filename in files:
            if not should_include(filename, all_jsonl):
                continue
            path = Path(root) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            sid, cwd, is_subagent, agent_nickname, agent_role = session_meta(path)
            yield {
                "path": str(path),
                "size": stat.st_size,
                "size_human": human_bytes(stat.st_size),
                "mtime": stat.st_mtime,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "session_id": sid or session_id_from_filename(path) or "unknown",
                "cwd": cwd or "",
                "project": project_dir_name(cwd),
                "project_dir_name": project_dir_name(cwd),
                "is_subagent": is_subagent,
                "agent_nickname": agent_nickname or "",
                "agent_role": agent_role or "",
            }


def meaningful_title(session_id: str, title: str) -> bool:
    text = title.strip()
    return bool(text) and text != session_id


def display_title(item: Dict[str, Any], title: str) -> str:
    if item.get("is_subagent") and title and not title.startswith(SUBAGENT_TITLE_PREFIX):
        return SUBAGENT_TITLE_PREFIX + title
    return title


def row_value(row: sqlite3.Row, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def source_is_subagent(source: Any, thread_source: Any) -> bool:
    if thread_source == "subagent":
        return True
    if isinstance(source, dict):
        return "subagent" in source or "subAgent" in source
    if not isinstance(source, str) or not source.strip():
        return False
    try:
        obj = json.loads(source)
    except Exception:
        return '"subagent"' in source or '"subAgent"' in source
    return isinstance(obj, dict) and ("subagent" in obj or "subAgent" in obj)


def sqlite_display_title(row: sqlite3.Row, edge_child: bool = False) -> Optional[Dict[str, str]]:
    sid = row_value(row, "id")
    if not isinstance(sid, str):
        return None

    raw_title = row_value(row, "title")
    raw_title = raw_title if isinstance(raw_title, str) else ""
    agent_nickname = row_value(row, "agent_nickname")
    agent_nickname = agent_nickname.strip() if isinstance(agent_nickname, str) else ""
    agent_role = row_value(row, "agent_role")
    agent_role = agent_role.strip() if isinstance(agent_role, str) else ""
    thread_source = row_value(row, "thread_source")
    source = row_value(row, "source")

    if agent_nickname and (source_is_subagent(source, thread_source) or edge_child):
        info = {
            "title": agent_nickname,
            "title_source": "sqlite_agent_nickname",
            "title_updated_at": "",
            "is_subagent": True,
            "agent_nickname": agent_nickname,
        }
        if agent_role:
            info["title_role"] = agent_role
            info["agent_role"] = agent_role
        return info

    if raw_title.strip():
        return {
            "title": raw_title,
            "title_source": "sqlite",
            "title_updated_at": "",
        }
    return None


def load_session_index_titles(codex_home: Path, session_ids: List[str]) -> Dict[str, Dict[str, str]]:
    wanted = set(session_ids)
    if not wanted:
        return {}
    index_path = codex_home / "session_index.jsonl"
    titles: Dict[str, Dict[str, str]] = {}
    try:
        with index_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                sid = obj.get("id")
                if sid not in wanted:
                    continue
                title = obj.get("thread_name")
                if not isinstance(title, str) or not title.strip():
                    continue
                # session_index.jsonl is append-like; the last matching row is
                # the closest offline approximation of the App sidebar title.
                titles[sid] = {
                    "title": title,
                    "title_source": "session_index",
                    "title_updated_at": str(obj.get("updated_at") or ""),
                }
    except OSError:
        return {}
    return titles


def sqlite_uri(path: Path) -> str:
    return "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"


def state_db_candidates(codex_home: Path, sqlite_home: Path) -> List[Path]:
    roots: List[Path] = []
    for root in (sqlite_home, codex_home, codex_home / "sqlite"):
        if root not in roots:
            roots.append(root)

    candidates: List[Path] = []
    for root in roots:
        try:
            matches = [path for path in root.glob("state_*.sqlite") if path.is_file()]
        except OSError:
            continue
        matches.sort(
            key=lambda path: (
                int(STATE_DB_RE.match(path.name).group(1)) if STATE_DB_RE.match(path.name) else -1,
                path.stat().st_mtime if path.exists() else 0,
            ),
            reverse=True,
        )
        for path in matches:
            if path not in candidates:
                candidates.append(path)
    return candidates


def load_sqlite_titles(codex_home: Path, sqlite_home: Path, session_ids: List[str]) -> Dict[str, Dict[str, str]]:
    wanted = [sid for sid in session_ids if sid]
    if not wanted:
        return {}

    titles: Dict[str, Dict[str, str]] = {}
    remaining = set(wanted)
    for db_path in state_db_candidates(codex_home, sqlite_home):
        if not remaining:
            break
        try:
            conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            conn.execute("PRAGMA query_only = ON")
            columns = [row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()]
            wanted_columns = [
                column
                for column in ("id", "title", "agent_nickname", "agent_role", "thread_source", "source")
                if column in columns
            ]
            if "id" not in wanted_columns:
                continue
            lookup = sorted(remaining)
            placeholders = ",".join("?" for _ in lookup)
            edge_children = set()
            edge_columns = [row[1] for row in conn.execute("PRAGMA table_info(thread_spawn_edges)").fetchall()]
            if "child_thread_id" in edge_columns:
                try:
                    edge_rows = conn.execute(
                        f"SELECT child_thread_id FROM thread_spawn_edges WHERE child_thread_id IN ({placeholders})",
                        lookup,
                    ).fetchall()
                    edge_children = {row[0] for row in edge_rows if isinstance(row[0], str)}
                except sqlite3.Error:
                    edge_children = set()
            rows = conn.execute(
                f"SELECT {', '.join(wanted_columns)} FROM threads WHERE id IN ({placeholders})",
                lookup,
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()

        for row in rows:
            sid = row_value(row, "id")
            info = sqlite_display_title(row, isinstance(sid, str) and sid in edge_children)
            if isinstance(sid, str) and info:
                titles[sid] = info
                remaining.discard(sid)
    return titles


def enrich_titles(entries: List[Dict[str, Any]], codex_home: Path, sqlite_home: Path, source: str) -> None:
    session_ids = [str(item.get("session_id") or "") for item in entries if item.get("session_id") != "unknown"]
    title_info: Dict[str, Dict[str, str]] = {}

    if source in ("auto", "session-index"):
        title_info.update(load_session_index_titles(codex_home, session_ids))

    if source in ("auto", "sqlite"):
        sqlite_titles = load_sqlite_titles(codex_home, sqlite_home, session_ids)
        if source == "sqlite":
            title_info = sqlite_titles
        else:
            for sid, info in sqlite_titles.items():
                current = title_info.get(sid)
                if (
                    info.get("title_source") == "sqlite_agent_nickname"
                    or current is None
                    or not meaningful_title(sid, current.get("title", ""))
                ):
                    title_info[sid] = info

    for item in entries:
        sid = str(item.get("session_id") or "")
        info = title_info.get(sid)
        if not info:
            if item.get("is_subagent") and item.get("agent_nickname"):
                item["title"] = display_title(item, str(item.get("agent_nickname") or ""))
                item["title_source"] = "session_meta_agent_nickname"
                item["title_updated_at"] = ""
                continue
            item["title"] = ""
            item["title_source"] = ""
            item["title_updated_at"] = ""
            continue
        item.update(info)
        item["title"] = display_title(item, str(item.get("title") or ""))


def print_table(entries: List[Dict[str, Any]], base: Path, show_cwd: bool, show_title: bool, top: int) -> None:
    print(f"Top {len(entries)} Codex JSONL file(s) by size under {base}:\n")
    print(f"{'#':>3}  {'size':>10}  {'modified':19}  {'project':18}  {'session_id':36}  path")
    print("-" * 132)
    for index, item in enumerate(entries, start=1):
        project = truncate_text(str(item.get("project") or ""), 18)
        print(
            f"{index:>3}  {str(item['size_human']):>10}  {item['modified']:19}  "
            f"{project:18}  {str(item['session_id'])[:36]:36}  {item['path']}"
        )
        if show_title and item.get("title"):
            source = item.get("title_source") or "unknown"
            print(f"     title[{source}]: {truncate_text(str(item['title']), 140)}")
        if show_cwd and item.get("cwd"):
            print(f"     cwd: {item['cwd']}")
    if top != 0:
        print("\nUse --top 0 to show all results.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List Codex JSONL files sorted by size. Defaults to rollout-*.jsonl under CODEX_HOME/sessions or ~/.codex/sessions."
    )
    parser.add_argument("--top", type=int, default=10, help="Number of largest files to display. Use 0 for all. Default: 10.")
    parser.add_argument("--base", default=default_sessions_base(), help="Base directory to scan. Default: CODEX_HOME/sessions when CODEX_HOME is set, otherwise ~/.codex/sessions.")
    parser.add_argument("--codex-home", help="Codex home used for title metadata lookup. Default: base parent when base is sessions/archived_sessions, then CODEX_HOME, otherwise ~/.codex.")
    parser.add_argument("--sqlite-home", help="SQLite state directory for title lookup. Default: sqlite_home config, CODEX_SQLITE_HOME, then codex home.")
    parser.add_argument("--show-cwd", action="store_true", help="Also print cwd from session_meta when available.")
    parser.add_argument("--show-title", action="store_true", help="Also print Codex App-style title from local read-only metadata when available.")
    parser.add_argument("--title-source", choices=("auto", "session-index", "sqlite", "none"), default="auto", help="Title lookup source for --show-title. Default: auto (session_index first, SQLite fallback).")
    parser.add_argument("--all-jsonl", action="store_true", help="Include all *.jsonl files under base, not only rollout-*.jsonl.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a table.")
    args = parser.parse_args()

    base = Path(os.path.expanduser(args.base))
    if not base.exists():
        if args.json:
            print(json.dumps({"base": str(base), "entries": []}, ensure_ascii=False, indent=2))
        else:
            print(f"No sessions directory found: {base}")
        return 0

    entries = sorted(iter_jsonl_files(base, args.all_jsonl), key=lambda item: int(item["size"]), reverse=True)
    limit = len(entries) if args.top == 0 else max(0, min(args.top, len(entries)))
    entries = entries[:limit]
    if args.show_title and args.title_source != "none":
        codex_home = infer_codex_home(base, args.codex_home)
        sqlite_home = infer_sqlite_home(codex_home, args.sqlite_home)
        enrich_titles(entries, codex_home, sqlite_home, args.title_source)

    if args.json:
        print(json.dumps({"base": str(base), "count": len(entries), "entries": entries}, ensure_ascii=False, indent=2))
        return 0

    if not entries:
        kind = "*.jsonl" if args.all_jsonl else "rollout-*.jsonl"
        print(f"No {kind} files found under {base}.")
        return 0

    print_table(entries, base, args.show_cwd, args.show_title, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
