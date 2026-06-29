#!/usr/bin/env python3
"""
cleanup_session_by_id.py - remove local Codex session artifacts by session ID.

Default mode is dry-run. Applying cleanup requires both --apply and --yes.

The intended use is removing finished sub-agent sessions whose results have
already been summarized into the parent thread. By default, only sub-agent
sessions with a closed spawn edge are allowed; normal sessions and open/unknown
sub-agents require explicit override flags.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
SQLITE_HOME_RE = re.compile(r"^\s*sqlite_home\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$")
STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")

STATE_DELETE_RULES = {
    "thread_dynamic_tools": ("thread_id",),
    "thread_spawn_edges": ("parent_thread_id", "child_thread_id"),
    "threads": ("id",),
}
OTHER_DELETE_RULES = {
    "logs": ("thread_id",),
    "thread_goals": ("thread_id",),
    "stage1_outputs": ("thread_id",),
}
AGENT_JOB_CANCEL_REASON = "agent job runner thread was deleted"
AGENT_JOB_ITEM_DELETE_REASON = "assigned thread was deleted"


def default_codex_home() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(os.path.expanduser(codex_home)).resolve()
    return Path(os.path.expanduser("~/.codex")).resolve()


def fallback_codex_home() -> Path:
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


def human(value: int) -> str:
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{value}B"


def sqlite_uri(path: Path) -> str:
    return "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"


def sqlite_rw_uri(path: Path) -> str:
    return "file:" + quote(path.as_posix(), safe="/:")


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


def read_session_meta(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "session_id": None,
        "cwd": "",
        "is_subagent": False,
        "agent_nickname": "",
        "agent_role": "",
    }
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
                if obj.get("type") != "session_meta":
                    break
                payload = obj.get("payload") or {}
                source = payload.get("source")
                thread_source = payload.get("thread_source") or payload.get("threadSource")
                source_nickname, source_role = source_agent_fields(source)
                nickname = payload.get("agent_nickname") or payload.get("agentNickname") or source_nickname
                role = payload.get("agent_role") or payload.get("agentRole") or source_role
                info.update(
                    {
                        "session_id": payload.get("id") if isinstance(payload.get("id"), str) else None,
                        "cwd": payload.get("cwd") if isinstance(payload.get("cwd"), str) else "",
                        "is_subagent": source_is_subagent(source, thread_source),
                        "agent_nickname": nickname.strip() if isinstance(nickname, str) and nickname.strip() else "",
                        "agent_role": role.strip() if isinstance(role, str) and role.strip() else "",
                    }
                )
                break
    except OSError:
        pass
    return info


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


def auxiliary_db_candidates(codex_home: Path, sqlite_home: Path) -> List[Path]:
    names = ("logs_2.sqlite", "goals_1.sqlite", "memories_1.sqlite")
    roots: List[Path] = []
    for root in (sqlite_home, codex_home, codex_home / "sqlite"):
        if root not in roots:
            roots.append(root)
    out: List[Path] = []
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file() and path not in out:
                out.append(path)
    return out


def sqlite_tables_and_columns(db_path: Path) -> Dict[str, Set[str]]:
    tables: Dict[str, Set[str]] = {}
    try:
        conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return tables
    try:
        try:
            table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.Error:
            return tables
        for (table,) in table_rows:
            try:
                tables[table] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            except sqlite3.Error:
                continue
    finally:
        conn.close()
    return tables


def placeholders(values: Sequence[str]) -> str:
    return ",".join("?" for _ in values)


def has_columns(tables: Dict[str, Set[str]], table: str, columns: Iterable[str]) -> bool:
    present = tables.get(table, set())
    return all(column in present for column in columns)


def agent_jobs_cancel_query(ids: Sequence[str]) -> Tuple[str, List[Any]]:
    id_marks = placeholders(ids)
    query = f"""
UPDATE agent_jobs
SET status = ?, updated_at = ?, completed_at = ?, last_error = ?
WHERE status IN (?, ?)
  AND id IN (
    SELECT item.job_id
    FROM agent_job_items AS item
    JOIN thread_spawn_edges AS edge ON edge.child_thread_id = item.assigned_thread_id
    WHERE item.status = ?
      AND item.assigned_thread_id IN ({id_marks})
      AND edge.parent_thread_id IN ({id_marks})
  )
"""
    return query, list(ids) + list(ids)


def agent_jobs_cancel_count(conn: sqlite3.Connection, tables: Dict[str, Set[str]], ids: Sequence[str]) -> int:
    if not ids:
        return 0
    required = (
        has_columns(tables, "agent_jobs", ("id", "status"))
        and has_columns(tables, "agent_job_items", ("job_id", "status", "assigned_thread_id"))
        and has_columns(tables, "thread_spawn_edges", ("parent_thread_id", "child_thread_id"))
    )
    if not required:
        return 0
    id_marks = placeholders(ids)
    query = f"""
SELECT COUNT(DISTINCT job.id)
FROM agent_jobs AS job
WHERE job.status IN (?, ?)
  AND job.id IN (
    SELECT item.job_id
    FROM agent_job_items AS item
    JOIN thread_spawn_edges AS edge ON edge.child_thread_id = item.assigned_thread_id
    WHERE item.status = ?
      AND item.assigned_thread_id IN ({id_marks})
      AND edge.parent_thread_id IN ({id_marks})
  )
"""
    params: List[Any] = ["pending", "running", "running"] + list(ids) + list(ids)
    try:
        return int(conn.execute(query, params).fetchone()[0])
    except sqlite3.Error:
        return 0


def count_delete_rows(db_path: Path, ids: Sequence[str], rules: Dict[str, Tuple[str, ...]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not db_path.is_file() or not ids:
        return counts
    tables = sqlite_tables_and_columns(db_path)
    if not tables:
        return counts
    try:
        conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return counts
    try:
        for table, columns in rules.items():
            present = [column for column in columns if column in tables.get(table, set())]
            if not present:
                continue
            where_parts = []
            params: List[str] = []
            for column in present:
                where_parts.append(f"{column} IN ({placeholders(ids)})")
                params.extend(ids)
            query = f"SELECT COUNT(*) FROM {table} WHERE " + " OR ".join(where_parts)
            try:
                count = int(conn.execute(query, params).fetchone()[0])
            except sqlite3.Error:
                count = 0
            if count:
                counts[table] = count
    finally:
        conn.close()
    return counts


def state_db_mentions_ids(db_path: Path, ids: Sequence[str]) -> bool:
    if not db_path.is_file() or not ids:
        return False
    tables = sqlite_tables_and_columns(db_path)
    checks = {
        "threads": ("id",),
        "thread_spawn_edges": ("parent_thread_id", "child_thread_id"),
        "agent_job_items": ("assigned_thread_id",),
    }
    try:
        conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return False
    try:
        for table, columns in checks.items():
            present = [column for column in columns if column in tables.get(table, set())]
            if not present:
                continue
            where_parts = []
            params: List[str] = []
            for column in present:
                where_parts.append(f"{column} IN ({placeholders(ids)})")
                params.extend(ids)
            try:
                query = f"SELECT 1 FROM {table} WHERE " + " OR ".join(where_parts) + " LIMIT 1"
                row = conn.execute(query, params).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                return True
    finally:
        conn.close()
    return False


def choose_canonical_state_db(state_dbs: Sequence[Path], requested_ids: Sequence[str]) -> Optional[Path]:
    for db_path in state_dbs:
        if state_db_mentions_ids(db_path, requested_ids):
            return db_path
    return state_dbs[0] if state_dbs else None


def count_state_db_changes(db_path: Path, ids: Sequence[str]) -> Dict[str, int]:
    counts = count_delete_rows(db_path, ids, STATE_DELETE_RULES)
    if not db_path.is_file() or not ids:
        return counts
    tables = sqlite_tables_and_columns(db_path)
    agent_columns = tables.get("agent_job_items", set())
    if "assigned_thread_id" not in agent_columns:
        return counts
    try:
        conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
    except sqlite3.Error:
        return counts
    try:
        cancelled = agent_jobs_cancel_count(conn, tables, ids)
        if cancelled:
            counts["agent_jobs_cancel"] = cancelled
        query = f"SELECT COUNT(*) FROM agent_job_items WHERE assigned_thread_id IN ({placeholders(ids)})"
        count = int(conn.execute(query, list(ids)).fetchone()[0])
        if count:
            counts["agent_job_items_unassign"] = count
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return counts


def delete_sqlite_rows(db_path: Path, ids: Sequence[str], rules: Dict[str, Tuple[str, ...]], vacuum: bool) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not db_path.is_file() or not ids:
        return counts
    tables = sqlite_tables_and_columns(db_path)
    conn = sqlite3.connect(sqlite_rw_uri(db_path), uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")
        for table, columns in rules.items():
            present = [column for column in columns if column in tables.get(table, set())]
            if not present:
                continue
            where_parts = []
            params: List[str] = []
            for column in present:
                where_parts.append(f"{column} IN ({placeholders(ids)})")
                params.extend(ids)
            query = f"DELETE FROM {table} WHERE " + " OR ".join(where_parts)
            cur = conn.execute(query, params)
            if cur.rowcount and cur.rowcount > 0:
                counts[table] = int(cur.rowcount)
        conn.commit()
        if vacuum and counts:
            conn.execute("VACUUM")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def apply_state_db_changes(db_path: Path, ids: Sequence[str], vacuum: bool) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not db_path.is_file() or not ids:
        return counts
    tables = sqlite_tables_and_columns(db_path)
    conn = sqlite3.connect(sqlite_rw_uri(db_path), uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")

        now = int(datetime.now(timezone.utc).timestamp())
        if (
            has_columns(tables, "agent_jobs", ("id", "status"))
            and has_columns(tables, "agent_job_items", ("job_id", "status", "assigned_thread_id"))
            and has_columns(tables, "thread_spawn_edges", ("parent_thread_id", "child_thread_id"))
        ):
            query, id_params = agent_jobs_cancel_query(ids)
            cur = conn.execute(
                query,
                [
                    "cancelled",
                    now,
                    now,
                    AGENT_JOB_CANCEL_REASON,
                    "pending",
                    "running",
                    "running",
                ]
                + id_params,
            )
            if cur.rowcount and cur.rowcount > 0:
                counts["agent_jobs_cancel"] = int(cur.rowcount)

        agent_columns = tables.get("agent_job_items", set())
        if "assigned_thread_id" in agent_columns:
            if "status" in agent_columns:
                set_parts: List[str] = ["status = ?", "assigned_thread_id = NULL"]
                params: List[Any] = ["pending"]
                if "updated_at" in agent_columns:
                    set_parts.append("updated_at = ?")
                    params.append(now)
                if "last_error" in agent_columns:
                    set_parts.append("last_error = ?")
                    params.append(AGENT_JOB_ITEM_DELETE_REASON)
                query = (
                    f"UPDATE agent_job_items SET {', '.join(set_parts)} "
                    f"WHERE assigned_thread_id IN ({placeholders(ids)}) AND status = ?"
                )
                cur = conn.execute(query, params + list(ids) + ["running"])
                if cur.rowcount and cur.rowcount > 0:
                    counts["agent_job_items_unassign"] = counts.get("agent_job_items_unassign", 0) + int(cur.rowcount)

            set_parts = ["assigned_thread_id = NULL"]
            params = []
            if "updated_at" in agent_columns:
                set_parts.append("updated_at = ?")
                params.append(now)
            query = (
                f"UPDATE agent_job_items SET {', '.join(set_parts)} "
                f"WHERE assigned_thread_id IN ({placeholders(ids)})"
            )
            cur = conn.execute(query, params + list(ids))
            if cur.rowcount and cur.rowcount > 0:
                counts["agent_job_items_unassign"] = counts.get("agent_job_items_unassign", 0) + int(cur.rowcount)

        for table, columns in STATE_DELETE_RULES.items():
            present = [column for column in columns if column in tables.get(table, set())]
            if not present:
                continue
            where_parts = []
            params = []
            for column in present:
                where_parts.append(f"{column} IN ({placeholders(ids)})")
                params.extend(ids)
            query = f"DELETE FROM {table} WHERE " + " OR ".join(where_parts)
            cur = conn.execute(query, params)
            if cur.rowcount and cur.rowcount > 0:
                counts[table] = int(cur.rowcount)

        conn.commit()
        if vacuum and counts:
            conn.execute("VACUUM")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def preflight_sqlite_writes(db_paths: Iterable[Path]) -> None:
    connections: List[sqlite3.Connection] = []
    try:
        for db_path in db_paths:
            conn: Optional[sqlite3.Connection] = None
            try:
                conn = sqlite3.connect(sqlite_rw_uri(db_path), uri=True)
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("BEGIN IMMEDIATE")
                connections.append(conn)
            except Exception:
                if conn is not None:
                    conn.close()
                raise
    finally:
        for conn in reversed(connections):
            try:
                conn.rollback()
            finally:
                conn.close()


def integrity_check(db_path: Path) -> str:
    try:
        conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
        try:
            return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:
        return f"ERROR: {exc}"


def session_index_hits(index_path: Path, ids: Set[str]) -> int:
    if not index_path.is_file():
        return 0
    hits = 0
    try:
        with index_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if any(obj.get(key) in ids for key in ("id", "thread_id", "threadId", "session_id", "sessionId")):
                    hits += 1
    except OSError:
        return 0
    return hits


def rewrite_session_index(index_path: Path, ids: Set[str]) -> int:
    if not index_path.is_file():
        return 0
    removed = 0
    fd, tmp_name = tempfile.mkstemp(prefix=index_path.name + ".", suffix=".tmp", dir=str(index_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with index_path.open("r", encoding="utf-8", errors="replace") as fin, tmp_path.open("w", encoding="utf-8") as fout:
            for line in fin:
                remove = False
                try:
                    obj = json.loads(line)
                    remove = any(obj.get(key) in ids for key in ("id", "thread_id", "threadId", "session_id", "sessionId"))
                except Exception:
                    remove = False
                if remove:
                    removed += 1
                    continue
                fout.write(line)
        shutil.copystat(index_path, tmp_path)
        os.replace(tmp_path, index_path)
        tmp_path = None
        return removed
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def find_rollout_files(codex_home: Path, base: Optional[str], ids: Set[str], scan_meta: bool, include_backups: bool) -> List[Path]:
    roots: List[Path] = []
    if base:
        roots.append(Path(os.path.expanduser(base)).resolve())
    else:
        roots.extend([codex_home / "sessions", codex_home / "archived_sessions"])
    out: List[Path] = []
    seen: Set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for current, _, files in os.walk(root):
            for filename in files:
                if not (filename.startswith("rollout-") and ".jsonl" in filename):
                    continue
                path = Path(current) / filename
                matched = False
                for sid in ids:
                    plain_name = f"{sid}.jsonl"
                    compressed_name = f"{plain_name}.zst"
                    if filename.endswith(plain_name) or filename.endswith(compressed_name):
                        matched = True
                        break
                    if include_backups and (
                        f"{plain_name}.orig" in filename or f"{compressed_name}.orig" in filename
                    ):
                        matched = True
                        break
                if not matched and scan_meta and filename.endswith(".jsonl"):
                    meta = read_session_meta(path)
                    matched = meta.get("session_id") in ids
                if matched and str(path) not in seen:
                    seen.add(str(path))
                    out.append(path)
    return sorted(out, key=lambda path: str(path))


def sqlite_spawn_edges(db_paths: Iterable[Path]) -> List[Dict[str, str]]:
    edges: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()
    for db_path in db_paths:
        if not db_path.is_file():
            continue
        tables = sqlite_tables_and_columns(db_path)
        edge_columns = tables.get("thread_spawn_edges", set())
        if not {"parent_thread_id", "child_thread_id"}.issubset(edge_columns):
            continue
        wanted_columns = [
            column
            for column in ("parent_thread_id", "child_thread_id", "status")
            if column in edge_columns
        ]
        try:
            conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            try:
                rows = conn.execute(f"SELECT {', '.join(wanted_columns)} FROM thread_spawn_edges").fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                parent_id = row["parent_thread_id"] if isinstance(row["parent_thread_id"], str) else ""
                child_id = row["child_thread_id"] if isinstance(row["child_thread_id"], str) else ""
                if not parent_id or not child_id:
                    continue
                status = row["status"] if "status" in row.keys() and row["status"] is not None else "unknown"
                item = {
                    "parent_thread_id": parent_id,
                    "child_thread_id": child_id,
                    "status": str(status),
                    "db_path": str(db_path),
                }
                key = (item["parent_thread_id"], item["child_thread_id"], item["status"], item["db_path"])
                if key not in seen:
                    seen.add(key)
                    edges.append(item)
        finally:
            conn.close()
    return edges


def expand_spawn_subtree(db_paths: Iterable[Path], requested_ids: Sequence[str]) -> Dict[str, Any]:
    edges = sqlite_spawn_edges(db_paths)
    children_by_parent: Dict[str, List[Dict[str, str]]] = {}
    for edge in edges:
        children_by_parent.setdefault(edge["parent_thread_id"], []).append(edge)

    expanded: List[str] = list(requested_ids)
    expanded_set = set(expanded)
    descendants: List[str] = []
    descendant_roots: Dict[str, Set[str]] = {}
    reachable_edges: List[Dict[str, str]] = []
    seen_edges: Set[Tuple[str, str, str, str]] = set()
    queue: List[Tuple[str, str]] = [(sid, sid) for sid in requested_ids]

    while queue:
        parent_id, root_id = queue.pop(0)
        for edge in children_by_parent.get(parent_id, []):
            edge_key = (
                edge["parent_thread_id"],
                edge["child_thread_id"],
                edge["status"],
                edge["db_path"],
            )
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                reachable_edges.append(edge)
            child_id = edge["child_thread_id"]
            descendant_roots.setdefault(child_id, set()).add(root_id)
            if child_id in expanded_set:
                continue
            expanded_set.add(child_id)
            expanded.append(child_id)
            descendants.append(child_id)
            queue.append((child_id, root_id))

    return {
        "expanded_ids": expanded,
        "descendant_ids": descendants,
        "descendant_roots": {sid: sorted(roots) for sid, roots in descendant_roots.items()},
        "spawn_tree_edges": reachable_edges,
    }


def sqlite_subagent_metadata(db_paths: Iterable[Path], ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return meta
    for db_path in db_paths:
        if not db_path.is_file():
            continue
        tables = sqlite_tables_and_columns(db_path)
        thread_columns = tables.get("threads", set())
        wanted_columns = [
            column
            for column in ("id", "source", "thread_source", "agent_nickname", "agent_role", "title")
            if column in thread_columns
        ]
        try:
            conn = sqlite3.connect(sqlite_uri(db_path), uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            if "id" in wanted_columns:
                try:
                    rows = conn.execute(
                        f"SELECT {', '.join(wanted_columns)} FROM threads WHERE id IN ({placeholders(ids)})",
                        list(ids),
                    ).fetchall()
                except sqlite3.Error:
                    rows = []
                for row in rows:
                    sid = row["id"]
                    source = row["source"] if "source" in row.keys() else None
                    thread_source = row["thread_source"] if "thread_source" in row.keys() else None
                    is_subagent = source_is_subagent(source, thread_source)
                    nickname = (
                        row["agent_nickname"]
                        if "agent_nickname" in row.keys() and isinstance(row["agent_nickname"], str)
                        else ""
                    )
                    role = row["agent_role"] if "agent_role" in row.keys() and isinstance(row["agent_role"], str) else ""
                    current = meta.setdefault(sid, {})
                    current.update(
                        {
                            "is_subagent": bool(current.get("is_subagent")) or is_subagent,
                            "agent_nickname": current.get("agent_nickname") or nickname,
                            "agent_role": current.get("agent_role") or role,
                            "sqlite_source_db": current.get("sqlite_source_db") or str(db_path),
                        }
                    )

            edge_columns = tables.get("thread_spawn_edges", set())
            if "child_thread_id" in edge_columns:
                wanted_edge_columns = [
                    column
                    for column in ("parent_thread_id", "child_thread_id", "status")
                    if column in edge_columns
                ]
                try:
                    edge_rows = conn.execute(
                        (
                            f"SELECT {', '.join(wanted_edge_columns)} FROM thread_spawn_edges "
                            f"WHERE child_thread_id IN ({placeholders(ids)})"
                        ),
                        list(ids),
                    ).fetchall()
                except sqlite3.Error:
                    edge_rows = []
                for row in edge_rows:
                    sid = row["child_thread_id"]
                    current = meta.setdefault(sid, {})
                    current["is_subagent"] = True
                    current["sqlite_source_db"] = current.get("sqlite_source_db") or str(db_path)
                    statuses = current.setdefault("spawn_edge_statuses", [])
                    status = row["status"] if "status" in row.keys() and row["status"] is not None else "unknown"
                    status_text = str(status)
                    if status_text not in statuses:
                        statuses.append(status_text)
                    status_sources = current.setdefault("spawn_edge_status_sources", [])
                    source_item = {"status": status_text, "db_path": str(db_path)}
                    if source_item not in status_sources:
                        status_sources.append(source_item)
                    parents = current.setdefault("parent_thread_ids", [])
                    parent_id = (
                        row["parent_thread_id"]
                        if "parent_thread_id" in row.keys() and isinstance(row["parent_thread_id"], str)
                        else ""
                    )
                    if parent_id and parent_id not in parents:
                        parents.append(parent_id)
        finally:
            conn.close()
    return meta


def unique_existing_sidecars(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            if candidate.exists() and str(candidate) not in seen:
                seen.add(str(candidate))
                out.append(candidate)
    return out


def sqlite_sidecar_paths(db_path: Path) -> List[Path]:
    return [Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")]


def capture_sqlite_sidecar_state(paths: Iterable[Path]) -> Dict[str, Dict[str, bool]]:
    state: Dict[str, Dict[str, bool]] = {}
    for db_path in paths:
        state[str(db_path)] = {
            str(sidecar): sidecar.exists()
            for sidecar in sqlite_sidecar_paths(db_path)
        }
    return state


def remove_new_sqlite_sidecars(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    removed: List[Dict[str, Any]] = []
    state = manifest.get("sqlite_sidecars_before") or {}
    if not isinstance(state, dict):
        return removed
    for sidecars in state.values():
        if not isinstance(sidecars, dict):
            continue
        for path_text, existed in sidecars.items():
            if existed:
                continue
            path = Path(path_text)
            if not path.exists():
                continue
            try:
                path.unlink()
                removed.append({"path": str(path), "removed": True})
            except Exception as exc:
                removed.append({"path": str(path), "removed": False, "error": str(exc)})
    return removed


def backup_path_for(source: Path, backup_dir: Path) -> Path:
    absolute = source.resolve()
    anchor = absolute.anchor
    parts = [part for part in absolute.parts if part not in (anchor, "/", "")]
    if anchor:
        safe_anchor = re.sub(r"[^A-Za-z0-9_.-]+", "_", anchor.strip("\\/")) or "root"
        parts.insert(0, safe_anchor)
    return backup_dir / "files" / Path(*parts)


def copy_backup(source: Path, backup_dir: Path) -> Optional[Path]:
    if not source.exists():
        return None
    target = backup_path_for(source, backup_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(target))
    return target


def restore_backups(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    restored: List[Dict[str, Any]] = []
    for item in reversed(manifest.get("backups", [])):
        source = Path(item["source"])
        backup = Path(item["backup"])
        if not backup.exists():
            restored.append({"source": str(source), "backup": str(backup), "restored": False, "error": "backup missing"})
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(backup), str(source))
            restored.append({"source": str(source), "backup": str(backup), "restored": True})
        except Exception as exc:
            restored.append({"source": str(source), "backup": str(backup), "restored": False, "error": str(exc)})
    return restored


def write_manifest(path: Optional[Path], manifest: Dict[str, Any]) -> None:
    if path is None:
        return
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_ids(values: Sequence[str]) -> List[str]:
    ids = []
    for value in values:
        sid = value.strip()
        if not sid:
            continue
        if not UUID_RE.match(sid):
            raise argparse.ArgumentTypeError(f"not a UUID: {value}")
        if sid not in ids:
            ids.append(sid)
    return ids


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


def process_targets_codex_home(args: str, target_codex_home: Optional[Path], env_text: str = "") -> bool:
    if target_codex_home is None:
        return True
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


def running_codex_processes(target_codex_home: Optional[Path] = None) -> List[Dict[str, str]]:
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
        is_codex_app = command_name == "Codex" or "/Codex.app/" in args
        is_codex_cli = command_name == "codex"
        if not (is_codex_app or is_codex_cli):
            continue
        env_text = process_environment_text(pid)
        if not process_targets_codex_home(args, target_codex_home, env_text):
            continue
        process_codex_home = extract_codex_home_from_env_text(env_text)
        matches.append(
            {
                "pid": str(pid),
                "command": command_name,
                "args": args,
                "process_codex_home": process_codex_home,
                "target_codex_home": str(target_codex_home) if target_codex_home else "",
            }
        )
    return matches


def format_processes(processes: Sequence[Dict[str, str]]) -> str:
    return "; ".join(
        f"pid={item.get('pid')} command={item.get('command')} args={item.get('args')}"
        for item in processes
    )


def build_plan(args: argparse.Namespace) -> Dict[str, Any]:
    codex_home = Path(os.path.expanduser(args.codex_home)).resolve() if args.codex_home else default_codex_home()
    sqlite_home = infer_sqlite_home(codex_home, args.sqlite_home)
    requested_ids = validate_ids(args.session_ids)
    requested_id_set = set(requested_ids)

    state_dbs = state_db_candidates(codex_home, sqlite_home)
    aux_dbs = auxiliary_db_candidates(codex_home, sqlite_home)
    canonical_state_db = choose_canonical_state_db(state_dbs, requested_ids)
    secondary_state_dbs = [path for path in state_dbs if path != canonical_state_db]
    spawn_tree = expand_spawn_subtree([canonical_state_db] if canonical_state_db else [], requested_ids)
    secondary_spawn_tree = expand_spawn_subtree(secondary_state_dbs, requested_ids)
    expanded_id_set = set(spawn_tree["expanded_ids"])
    stale_descendant_candidates = [
        sid for sid in secondary_spawn_tree["descendant_ids"] if sid not in expanded_id_set
    ]
    stale_spawn_tree_edges = [
        edge
        for edge in secondary_spawn_tree["spawn_tree_edges"]
        if edge.get("child_thread_id") in stale_descendant_candidates
        or edge.get("parent_thread_id") in stale_descendant_candidates
    ]
    ids = list(spawn_tree["expanded_ids"])
    id_set = set(ids)
    descendant_id_set = set(spawn_tree["descendant_ids"])

    rollout_files = find_rollout_files(codex_home, args.base, id_set, args.scan_meta, not args.no_rollout_backups)
    active_rollouts = [path for path in rollout_files if path.name.endswith(".jsonl")]
    file_meta: Dict[str, Dict[str, Any]] = {}
    for path in active_rollouts:
        meta = read_session_meta(path)
        sid = meta.get("session_id")
        if isinstance(sid, str) and sid in id_set:
            file_meta.setdefault(sid, {}).update(meta)

    sqlite_meta = sqlite_subagent_metadata(state_dbs, ids)
    session_index = codex_home / "session_index.jsonl"

    sessions: Dict[str, Dict[str, Any]] = {}
    for sid in ids:
        meta = {}
        meta.update(file_meta.get(sid, {}))
        sqlite_info = sqlite_meta.get(sid, {})
        is_subagent = bool(meta.get("is_subagent")) or bool(sqlite_info.get("is_subagent"))
        nickname = meta.get("agent_nickname") or sqlite_info.get("agent_nickname") or ""
        role = meta.get("agent_role") or sqlite_info.get("agent_role") or ""
        spawn_edge_statuses = list(sqlite_info.get("spawn_edge_statuses") or [])
        spawn_edge_status_sources = list(sqlite_info.get("spawn_edge_status_sources") or [])
        parent_thread_ids = list(sqlite_info.get("parent_thread_ids") or [])
        normalized_statuses = {str(status).lower() for status in spawn_edge_statuses}
        is_closed_subagent = is_subagent and "closed" in normalized_statuses
        concrete_statuses = {status for status in normalized_statuses if status}
        has_status_conflict = len(concrete_statuses) > 1
        status_warning = ""
        if sid in descendant_id_set:
            if has_status_conflict:
                status_warning = "descendant has conflicting spawn edge statuses"
            elif not is_closed_subagent:
                status_warning = "descendant is not closed; included because root subtree cleanup follows Codex delete semantics"
        sessions[sid] = {
            "session_id": sid,
            "requested": sid in requested_id_set,
            "included_by_spawn_tree": sid in descendant_id_set,
            "spawn_tree_roots": list(spawn_tree["descendant_roots"].get(sid, [])),
            "is_subagent": is_subagent,
            "is_closed_subagent": is_closed_subagent,
            "has_spawn_edge_status_conflict": has_status_conflict,
            "status_warning": status_warning,
            "spawn_edge_statuses": spawn_edge_statuses,
            "spawn_edge_status_sources": spawn_edge_status_sources,
            "parent_thread_ids": parent_thread_ids,
            "agent_nickname": nickname,
            "agent_role": role,
            "rollout_files": [str(path) for path in rollout_files if sid in path.name],
        }

    refused_not_subagent = [
        sid
        for sid, info in sessions.items()
        if info.get("requested") and not info.get("is_subagent") and not args.allow_non_subagent
    ]
    refused_not_closed = [
        sid
        for sid, info in sessions.items()
        if info.get("requested")
        and info.get("is_subagent")
        and not info.get("is_closed_subagent")
        and not args.allow_open_subagent
    ]
    refused_status_conflict = [
        sid
        for sid, info in sessions.items()
        if info.get("requested") and info.get("has_spawn_edge_status_conflict")
    ]
    refused = refused_not_subagent[:]
    for sid in refused_not_closed + refused_status_conflict:
        if sid not in refused:
            refused.append(sid)

    db_counts: Dict[str, Dict[str, int]] = {}
    for db_path in state_dbs:
        counts = count_state_db_changes(db_path, ids)
        if counts:
            db_counts[str(db_path)] = counts
    for db_path in aux_dbs:
        counts = count_delete_rows(db_path, ids, OTHER_DELETE_RULES)
        if counts:
            db_counts[str(db_path)] = counts

    index_hits = session_index_hits(session_index, id_set)
    files_total_bytes = sum(file_size(path) for path in rollout_files)

    return {
        "codex_home": str(codex_home),
        "sqlite_home": str(sqlite_home),
        "canonical_state_db": str(canonical_state_db) if canonical_state_db else "",
        "secondary_state_dbs": [str(path) for path in secondary_state_dbs],
        "requested_session_ids": requested_ids,
        "session_ids": ids,
        "descendant_session_ids": list(spawn_tree["descendant_ids"]),
        "spawn_tree_edges": list(spawn_tree["spawn_tree_edges"]),
        "stale_spawn_descendant_candidates": stale_descendant_candidates,
        "stale_spawn_tree_edges": stale_spawn_tree_edges,
        "sessions": sessions,
        "refused": refused,
        "refused_not_subagent": refused_not_subagent,
        "refused_not_closed": refused_not_closed,
        "refused_status_conflict": refused_status_conflict,
        "rollout_files": [{"path": str(path), "bytes": file_size(path), "size": human(file_size(path))} for path in rollout_files],
        "rollout_files_total_bytes": files_total_bytes,
        "rollout_files_total_size": human(files_total_bytes),
        "session_index": {"path": str(session_index), "matching_lines": index_hits},
        "sqlite_counts": db_counts,
        "state_dbs": [str(path) for path in state_dbs],
        "auxiliary_dbs": [str(path) for path in aux_dbs],
    }


def print_plan(plan: Dict[str, Any]) -> None:
    print("Codex session cleanup plan")
    print(f"Codex home : {plan['codex_home']}")
    print(f"SQLite home: {plan['sqlite_home']}")
    if plan.get("canonical_state_db"):
        print(f"State DB   : {plan['canonical_state_db']} (canonical cleanup source)")
    if plan.get("secondary_state_dbs"):
        print(f"Secondary : {len(plan['secondary_state_dbs'])} state DB(s) used for diagnostics only")
    print(f"Requested  : {', '.join(plan.get('requested_session_ids') or plan['session_ids'])}")
    if plan.get("descendant_session_ids"):
        print(f"Descendants: {', '.join(plan['descendant_session_ids'])}")
    print(f"Cleanup IDs: {', '.join(plan['session_ids'])}")
    print()
    for sid, info in plan["sessions"].items():
        relation = "requested" if info.get("requested") else "spawn-descendant"
        label = f"{relation}, " + ("subagent" if info.get("is_subagent") else "NOT subagent")
        if info.get("is_subagent"):
            statuses = info.get("spawn_edge_statuses") or ["unknown"]
            label += f", status={','.join(str(status) for status in statuses)}"
        if info.get("has_spawn_edge_status_conflict"):
            label += ", STATUS-CONFLICT"
        if info.get("status_warning"):
            label += ", WARNING"
        if info.get("spawn_tree_roots"):
            label += f", roots={','.join(str(root) for root in info['spawn_tree_roots'])}"
        nickname = f" ({info.get('agent_nickname')})" if info.get("agent_nickname") else ""
        print(f"- {sid}: {label}{nickname}")
        if info.get("status_warning"):
            print(f"    warning: {info['status_warning']}")
    if plan.get("refused_not_subagent"):
        print("\nRefused by default because not confirmed as sub-agent:")
        for sid in plan["refused_not_subagent"]:
            print(f"  - {sid}")
        print("Use --allow-non-subagent only if you explicitly want to delete normal sessions.")
    if plan.get("refused_not_closed"):
        print("\nRefused by default because sub-agent spawn edge is not closed:")
        for sid in plan["refused_not_closed"]:
            print(f"  - {sid}")
        print("Use --allow-open-subagent only after confirming the child session is disposable.")
    if plan.get("refused_status_conflict"):
        print("\nRefused because multiple state DBs report conflicting spawn edge statuses:")
        for sid in plan["refused_status_conflict"]:
            sources = plan["sessions"].get(sid, {}).get("spawn_edge_status_sources") or []
            detail = ", ".join(f"{item.get('status')}@{item.get('db_path')}" for item in sources)
            print(f"  - {sid}: {detail}")
    if plan.get("stale_spawn_descendant_candidates"):
        print("\nSecondary state DB descendant candidates not cleaned:")
        for sid in plan["stale_spawn_descendant_candidates"]:
            print(f"  - {sid}")
        print("These came from non-canonical state DBs and are diagnostic only.")
    print("\nRollout files:")
    if plan["rollout_files"]:
        for item in plan["rollout_files"]:
            print(f"  - {item['size']:>10}  {item['path']}")
        print(f"  total: {plan['rollout_files_total_size']}")
    else:
        print("  none found")
    print("\nsession_index:")
    print(f"  {plan['session_index']['matching_lines']} matching line(s) in {plan['session_index']['path']}")
    print("\nSQLite rows:")
    if plan["sqlite_counts"]:
        for db_path, counts in plan["sqlite_counts"].items():
            detail = ", ".join(f"{table}={count}" for table, count in counts.items())
            print(f"  - {db_path}: {detail}")
    else:
        print("  none found")


def delete_rollout_files(plan: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    for item in plan["rollout_files"]:
        path = Path(item["path"])
        if path.exists():
            path.unlink()
            manifest["actions"].append({"action": "delete_file", "path": str(path), "bytes": item["bytes"]})


def rewrite_session_index_for_plan(plan: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    if not plan["session_index"]["matching_lines"]:
        return
    index_path = Path(plan["session_index"]["path"])
    removed = rewrite_session_index(index_path, set(plan["session_ids"]))
    manifest["actions"].append({"action": "rewrite_session_index", "path": str(index_path), "removed_lines": removed})


def apply_sqlite_changes_for_plan(args: argparse.Namespace, plan: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    for db_path_text in plan["sqlite_counts"].keys():
        db_path = Path(db_path_text)
        if db_path.name.startswith("state_"):
            counts = apply_state_db_changes(db_path, plan["session_ids"], args.vacuum)
            action = "update_state_sqlite_rows"
        else:
            counts = delete_sqlite_rows(db_path, plan["session_ids"], OTHER_DELETE_RULES, args.vacuum)
            action = "delete_sqlite_rows"
        manifest["actions"].append({"action": action, "path": str(db_path), "changed": counts})
        result = integrity_check(db_path)
        manifest["integrity"][str(db_path)] = result
        if result != "ok":
            raise RuntimeError(f"SQLite integrity_check failed for {db_path}: {result}")


def apply_cleanup(args: argparse.Namespace, plan: Dict[str, Any]) -> Dict[str, Any]:
    if plan["refused"]:
        raise RuntimeError("refusing to clean protected session(s): " + ", ".join(plan["refused"]))
    if not args.apply or not args.yes:
        raise RuntimeError("cleanup requires both --apply and --yes")
    codex_home = Path(plan["codex_home"])
    codex_processes = running_codex_processes(codex_home)
    if codex_processes and not args.allow_running_codex:
        raise RuntimeError(
            "Codex appears to be running for the target Codex home; close that Codex instance before applying cleanup "
            "or re-run with --allow-running-codex after accepting the risk. "
            + format_processes(codex_processes[:5])
        )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cleanup_backup_enabled = not getattr(args, "no_cleanup_backup", False)
    backup_dir: Optional[Path] = None
    manifest_path: Optional[Path] = None

    manifest: Dict[str, Any] = {
        "schema": "codex-session-compress/session-cleanup/v1",
        "created_at": timestamp,
        "plan": plan,
        "cleanup_backup_enabled": cleanup_backup_enabled,
        "manifest_path": "",
        "backups": [],
        "actions": [],
        "integrity": {},
        "codex_processes": codex_processes,
    }

    paths_to_backup: List[Path] = []
    for item in plan["rollout_files"]:
        paths_to_backup.append(Path(item["path"]))
    index_path = Path(plan["session_index"]["path"])
    if plan["session_index"]["matching_lines"]:
        paths_to_backup.append(index_path)
    db_paths = [Path(path) for path in plan["sqlite_counts"].keys()]
    manifest["sqlite_sidecars_before"] = capture_sqlite_sidecar_state(db_paths)
    paths_to_backup.extend(unique_existing_sidecars(db_paths))
    preflight_sqlite_writes(db_paths)

    if cleanup_backup_enabled:
        backup_root = Path(os.path.expanduser(args.backup_root)).resolve() if args.backup_root else codex_home / "backups"
        backup_dir = backup_root / f"session-cleanup-{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        seen: Set[str] = set()
        for path in paths_to_backup:
            if str(path) in seen:
                continue
            seen.add(str(path))
            backup_path = copy_backup(path, backup_dir)
            if backup_path:
                manifest["backups"].append({"source": str(path), "backup": str(backup_path), "bytes": file_size(path)})
        manifest_path = backup_dir / "cleanup-manifest.json"
        manifest["manifest_path"] = str(manifest_path)
    else:
        manifest["warning"] = (
            "cleanup backup disabled by --no-cleanup-backup; "
            "restore_cleanup_manifest.py cannot restore this cleanup"
        )
    write_manifest(manifest_path, manifest)

    try:
        if cleanup_backup_enabled:
            delete_rollout_files(plan, manifest)
            rewrite_session_index_for_plan(plan, manifest)
            apply_sqlite_changes_for_plan(args, plan, manifest)
        else:
            apply_sqlite_changes_for_plan(args, plan, manifest)
            rewrite_session_index_for_plan(plan, manifest)
            delete_rollout_files(plan, manifest)

        write_manifest(manifest_path, manifest)
    except Exception as exc:
        manifest["error"] = str(exc)
        if cleanup_backup_enabled:
            manifest["restored_backups"] = restore_backups(manifest)
            manifest["removed_new_sqlite_sidecars"] = remove_new_sqlite_sidecars(manifest)
            write_manifest(manifest_path, manifest)
            raise RuntimeError(f"cleanup failed and attempted restore from backups; manifest: {manifest_path}") from exc
        write_manifest(manifest_path, manifest)
        raise RuntimeError("cleanup failed without backup; no automatic restore is available") from exc

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or remove local Codex session artifacts by session ID.")
    parser.add_argument("session_ids", nargs="+", help="Codex session UUID(s) to clean")
    parser.add_argument("--codex-home", default=None, help="Codex home. Default: CODEX_HOME or ~/.codex.")
    parser.add_argument("--sqlite-home", default=None, help="SQLite state directory. Default: sqlite_home config, CODEX_SQLITE_HOME, then codex home.")
    parser.add_argument("--base", default=None, help="Override rollout scan base. Default: codex_home/sessions and codex_home/archived_sessions.")
    parser.add_argument("--scan-meta", action="store_true", help="Scan rollout session_meta.id if filename matching is insufficient.")
    parser.add_argument("--no-rollout-backups", action="store_true", help="Do not remove rollout sidecar backups such as .jsonl.orig or .jsonl.zst.orig.")
    parser.add_argument("--allow-non-subagent", action="store_true", help="Allow deleting sessions not identified as sub-agent sessions.")
    parser.add_argument("--allow-open-subagent", action="store_true", help="Allow deleting sub-agent sessions whose spawn edge status is open or unknown.")
    parser.add_argument("--backup-root", default=None, help="Directory for cleanup backups. Default: codex_home/backups.")
    parser.add_argument("--no-cleanup-backup", action="store_true", help="Irreversibly apply cleanup without copying files or SQLite DBs to codex_home/backups.")
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM on SQLite DBs after row deletion.")
    parser.add_argument("--allow-running-codex", action="store_true", help="Allow --apply even when Codex App or codex CLI processes appear to be using the target Codex home.")
    parser.add_argument("--apply", action="store_true", help="Apply the cleanup. Without this, only a dry-run plan is printed.")
    parser.add_argument("--yes", action="store_true", help="Required together with --apply.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable plan/result JSON.")
    args = parser.parse_args()

    if args.no_cleanup_backup and args.backup_root:
        print("error: --backup-root cannot be used together with --no-cleanup-backup", file=sys.stderr)
        return 2

    try:
        plan = build_plan(args)
    except argparse.ArgumentTypeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json and not args.apply:
        print(json.dumps({"dry_run": True, "plan": plan}, ensure_ascii=False, indent=2))
    elif not args.json:
        print_plan(plan)

    if not args.apply:
        if not args.json:
            print("\n[dry-run] no files or databases modified. Re-run with --apply --yes to delete.")
        return 1 if plan["refused"] else 0

    try:
        manifest = apply_cleanup(args, plan)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"dry_run": False, "manifest": manifest}, ensure_ascii=False, indent=2))
    else:
        print("\nCleanup applied.")
        if manifest.get("cleanup_backup_enabled"):
            print(f"Backup manifest: {manifest['manifest_path']}")
        else:
            print("Cleanup backup: disabled (--no-cleanup-backup); this cleanup is not restorable by restore_cleanup_manifest.py")
        for db_path, result in manifest["integrity"].items():
            print(f"Integrity {db_path}: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
