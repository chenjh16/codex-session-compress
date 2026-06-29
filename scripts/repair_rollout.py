#!/usr/bin/env python3
"""
repair_rollout.py - semantic-only physical compression for Codex rollouts.

The compressor preserves the model-visible resume baseline Codex reconstructs
from a modern full compaction and uses the remaining budget for App-visible
historical breadcrumbs:

  1. The first session_meta line.
  2. The newest full compacted checkpoint, defined as a compacted item whose
     payload has replacement_history.
  3. Every rollout line after that checkpoint.

Those lines form the mandatory semantic segment and are kept even if they exceed
the requested goal size. The remaining goal-size budget is used for historical
breadcrumbs before the checkpoint. App-visible event_msg lines and
pre-checkpoint turn_context lines are prioritized first, alternating from the
first visible history side and from the tail before compaction. If budget remains,
response_item lines are retained as raw model breadcrumb detail. The omitted
middle is represented by a tiny explicit synthetic maintenance turn inserted at
the pre-checkpoint elision gap. It uses a user_message bubble as the visible cut
marker and an agent_message bubble for the compression result, while remaining
older than the full checkpoint that drives active resume reconstruction.

Rollouts without a full compacted checkpoint are not modified; the script exits
with an error because semantic reconstruction would not be provably preserved.
"""

import argparse
import json
import locale
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

V8_MAX_STRING = 536_870_888
DEFAULT_GOAL_SIZE = "100MB"
SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?|bytes?)?\s*$", re.IGNORECASE)
SYNTHETIC_TURN_ID_PREFIX = "codex-session-compress-elision-"


def default_sessions_base():
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return os.path.join(os.path.expanduser(codex_home), "sessions")
    return os.path.expanduser("~/.codex/sessions")


def human(value):
    n = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return "%s%s" % (("%.1f" % n) if unit != "B" else str(int(n)), unit)
        n /= 1024


def parse_size(value):
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


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def scan(path):
    info = {
        "lines": 0,
        "bad_json": 0,
        "bytes": os.path.getsize(path),
        "meta_id": None,
        "session_meta_index": None,
        "session_meta_line": None,
        "first_is_meta": False,
        "last_compacted": -1,
        "compacted_count": 0,
        "last_full_compacted": -1,
        "full_compacted_count": 0,
        "user_msg_lines": [],
        "event_user_msg_lines": [],
        "event_msg_lines": [],
        "turn_context_lines": [],
        "response_item_lines": [],
        "line_bytes": [],
    }
    with open(path, "r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            info["line_bytes"].append(len(line.encode("utf-8")))
            text = line.strip()
            if not text:
                continue
            info["lines"] += 1
            try:
                obj = json.loads(text)
            except Exception:
                info["bad_json"] += 1
                continue
            kind = obj.get("type")
            if kind == "session_meta":
                if info["meta_id"] is None:
                    info["meta_id"] = (obj.get("payload") or {}).get("id")
                    info["session_meta_index"] = index
                    info["session_meta_line"] = line
                    info["first_is_meta"] = index == 0
            elif kind == "compacted":
                info["last_compacted"] = index
                info["compacted_count"] += 1
                payload = obj.get("payload") or {}
                if isinstance(payload.get("replacement_history"), list):
                    info["last_full_compacted"] = index
                    info["full_compacted_count"] += 1
            elif kind == "response_item":
                info["response_item_lines"].append(index)
                payload = obj.get("payload") or {}
                if payload.get("type") == "message" and payload.get("role") == "user":
                    info["user_msg_lines"].append(index)
            elif kind == "event_msg":
                info["event_msg_lines"].append(index)
                payload = obj.get("payload") or {}
                if payload.get("type") == "user_message":
                    info["event_user_msg_lines"].append(index)
            elif kind == "turn_context":
                info["turn_context_lines"].append(index)
    return info


def rollout_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


def make_event_line(payload):
    return dump({"timestamp": rollout_timestamp(), "type": "event_msg", "payload": payload}) + "\n"


def normalize_locale_tag(value):
    if not value:
        return ""
    return str(value).split(".", 1)[0].split("@", 1)[0].replace("_", "-").lower()


def detect_os_message_language():
    candidates = []
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
            )
            if result.returncode == 0:
                candidates.append(result.stdout.strip())
        except Exception:
            pass
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        candidates.append(os.environ.get(name))
    try:
        candidates.append(locale.getlocale()[0])
    except Exception:
        pass
    for value in candidates:
        tag = normalize_locale_tag(value)
        if tag:
            if tag.startswith("zh"):
                return "zh"
            break
    return "en"


def make_elision_texts(omitted_items, omitted_bytes, checkpoint_line, mandatory_bytes, target_bytes, language):
    if language == "zh":
        user_text = (
            "[codex-session-compress 合成标记] 此处有一段旧历史 rollout 因压缩被省略。"
            "这条用户气泡由压缩工具写入，用于标记可见历史的截断位置，不是原始用户指令。"
        )
        agent_text = (
            "codex-session-compress 已省略 %d 行旧历史 rollout，约 %s。"
            "最新 full CompactedItem.replacement_history checkpoint 位于原始第 %d 行；"
            "该 checkpoint 及其后的 active suffix 已完整保留。"
            "checkpoint 前的 event_msg 与 turn_context breadcrumb 已从两端按 GOAL_SIZE 尽量保留，"
            "剩余预算也可保留原始 response_item breadcrumb；超出预算的中段已物理删除。"
            "mandatory_segment=%s goal_size=%s。"
        ) % (omitted_items, human(omitted_bytes), checkpoint_line + 1, human(mandatory_bytes), human(target_bytes))
        return user_text, agent_text
    user_text = (
        "[codex-session-compress synthetic marker] A span of older rollout history was omitted here by compression. "
        "This user bubble was written by the compression tool to mark the visible history cut point; "
        "it is not an original user instruction."
    )
    agent_text = (
        "codex-session-compress omitted %d historical rollout line(s), about %s. "
        "The newest full CompactedItem.replacement_history checkpoint was original line %d; "
        "that checkpoint and the following active suffix were preserved completely. "
        "Before the checkpoint, event_msg and turn_context breadcrumbs were retained from both ends within GOAL_SIZE; "
        "remaining budget may also retain original response_item breadcrumbs. "
        "The over-budget middle was physically removed. mandatory_segment=%s goal_size=%s."
    ) % (omitted_items, human(omitted_bytes), checkpoint_line + 1, human(mandatory_bytes), human(target_bytes))
    return user_text, agent_text


def make_elision_lines(omitted_items, omitted_bytes, checkpoint_line, mandatory_bytes, target_bytes, language):
    user_text, agent_text = make_elision_texts(
        omitted_items,
        omitted_bytes,
        checkpoint_line,
        mandatory_bytes,
        target_bytes,
        language,
    )
    turn_id = "%s%d" % (SYNTHETIC_TURN_ID_PREFIX, checkpoint_line + 1)
    return "".join(
        [
            make_event_line(
                {
                    "type": "task_started",
                    "turn_id": turn_id,
                    "model_context_window": None,
                    "collaboration_mode_kind": "default",
                }
            ),
            make_event_line(
                {
                    "type": "user_message",
                    "client_id": turn_id,
                    "message": user_text,
                    "images": [],
                    "local_images": [],
                    "text_elements": [],
                }
            ),
            make_event_line(
                {
                    "type": "agent_message",
                    "message": agent_text,
                    "phase": "commentary",
                    "memory_citation": None,
                }
            ),
            make_event_line(
                {
                    "type": "task_complete",
                    "turn_id": turn_id,
                    "last_agent_message": agent_text,
                }
            ),
        ]
    )


def select_two_ended(candidates, line_bytes, budget, initial_keep=None):
    keep = set(initial_keep or [])
    add_order = []
    side_by_index = {}
    used = sum(line_bytes[index] for index in keep)
    left = 0
    right = len(candidates) - 1
    take_left = True
    while left <= right:
        if take_left:
            pos = left
            left += 1
            side = "left"
        else:
            pos = right
            right -= 1
            side = "right"
        take_left = not take_left
        index = candidates[pos]
        if index in keep:
            continue
        size = line_bytes[index]
        if used + size <= budget:
            keep.add(index)
            add_order.append(index)
            side_by_index[index] = side
            used += size
    return keep, add_order, used, side_by_index


def find_placeholder_insert_index(candidates, optional_keep, omitted, checkpoint, candidate_side):
    if not omitted:
        return None, None
    left_kept = sorted(
        index
        for index in optional_keep
        if index in candidates and index < checkpoint and candidate_side.get(index, "left") == "left"
    )
    if left_kept:
        left_anchor = max(left_kept)
        after_left = [index for index in candidates if index > left_anchor]
        if after_left:
            return min(after_left), "pre-checkpoint-elision-gap"
        return checkpoint, "pre-checkpoint-elision-gap"
    return min(omitted), "pre-checkpoint-elision-gap"


def build_semantic_checkpoint_plan(info, target_bytes):
    checkpoint = info.get("last_full_compacted", -1)
    meta_index = info.get("session_meta_index")
    line_bytes = info.get("line_bytes") or []
    if checkpoint < 0 or meta_index is None:
        return None
    placeholder_language = detect_os_message_language()

    mandatory_indices = set(range(checkpoint, len(line_bytes)))
    mandatory_indices.add(meta_index)
    mandatory_bytes = sum(line_bytes[index] for index in mandatory_indices)

    first_user = None
    for index in sorted(info.get("event_user_msg_lines", []) + info.get("user_msg_lines", [])):
        if index < checkpoint:
            first_user = index
            break

    first_history = None
    pre_checkpoint_ui_lines = [
        index
        for index in (info.get("event_msg_lines", []) + info.get("turn_context_lines", []))
        if index < checkpoint
    ]
    if pre_checkpoint_ui_lines:
        first_history = min(pre_checkpoint_ui_lines)
    elif first_user is not None:
        first_history = first_user

    ui_candidates = []
    response_candidates = []
    if first_history is not None:
        ui_candidates = sorted(
            index
            for index in (info.get("event_msg_lines", []) + info.get("turn_context_lines", []))
            if first_history <= index < checkpoint
        )
        response_candidates = [
            index
            for index in info.get("response_item_lines", [])
            if first_history <= index < checkpoint
        ]

    optional_keep = set()
    candidate_side = {}
    mode = "mandatory-exceeds-goal" if mandatory_bytes > target_bytes else "balanced-history"
    placeholder_probe = make_elision_lines(0, 0, checkpoint, mandatory_bytes, target_bytes, placeholder_language)
    placeholder_budget = len(placeholder_probe.encode("utf-8"))

    if first_user is not None or ui_candidates or response_candidates:
        if mandatory_bytes > target_bytes:
            if first_user is not None:
                optional_keep.add(first_user)
                candidate_side[first_user] = "left"
        else:
            budget = max(0, target_bytes - mandatory_bytes - placeholder_budget)
            ui_keep, _, ui_used, ui_sides = select_two_ended(ui_candidates, line_bytes, budget)
            optional_keep.update(ui_keep)
            candidate_side.update(ui_sides)
            response_budget = max(0, budget - ui_used)
            response_keep, response_add_order, _, response_sides = select_two_ended(
                response_candidates,
                line_bytes,
                response_budget,
            )
            optional_keep.update(response_keep)
            candidate_side.update(response_sides)

            final_indices_probe = set(mandatory_indices)
            final_indices_probe.update(optional_keep)
            final_size_probe = sum(line_bytes[index] for index in final_indices_probe) + placeholder_budget
            while response_add_order and final_size_probe > target_bytes:
                index = response_add_order.pop()
                if index not in optional_keep:
                    continue
                optional_keep.remove(index)
                final_size_probe -= line_bytes[index]

    breadcrumb_candidates = sorted(set(ui_candidates) | set(response_candidates))
    historical_indices = [
        index
        for index in range(0, checkpoint)
        if index != meta_index
    ]
    omitted = [index for index in historical_indices if index not in optional_keep]
    omitted_bytes = sum(line_bytes[index] for index in omitted)
    breadcrumb_omitted = [index for index in breadcrumb_candidates if index not in optional_keep]
    placeholder_line = None
    insert_before = None
    if omitted:
        placeholder_line = make_elision_lines(
            len(omitted),
            omitted_bytes,
            checkpoint,
            mandatory_bytes,
            target_bytes,
            placeholder_language,
        )
        insert_before, placeholder_position = find_placeholder_insert_index(
            historical_indices,
            optional_keep,
            omitted,
            checkpoint,
            candidate_side,
        )
    else:
        placeholder_position = None

    final_indices = set(mandatory_indices)
    final_indices.update(optional_keep)
    final_size = sum(line_bytes[index] for index in final_indices)
    if placeholder_line:
        final_size += len(placeholder_line.encode("utf-8"))

    if mandatory_bytes <= target_bytes and final_size > target_bytes:
        removable = sorted((index for index in optional_keep if index in response_candidates), reverse=True)
        for index in removable:
            if final_size <= target_bytes:
                break
            optional_keep.remove(index)
            final_indices.remove(index)
            final_size -= line_bytes[index]
        removable = sorted((index for index in optional_keep if index in ui_candidates), reverse=True)
        for index in removable:
            if final_size <= target_bytes:
                break
            optional_keep.remove(index)
            final_indices.remove(index)
            final_size -= line_bytes[index]
        omitted = [index for index in historical_indices if index not in optional_keep]
        omitted_bytes = sum(line_bytes[index] for index in omitted)
        breadcrumb_omitted = [index for index in breadcrumb_candidates if index not in optional_keep]
        if omitted:
            placeholder_line = make_elision_lines(
                len(omitted),
                omitted_bytes,
                checkpoint,
                mandatory_bytes,
                target_bytes,
                placeholder_language,
            )
            insert_before, placeholder_position = find_placeholder_insert_index(
                historical_indices,
                optional_keep,
                omitted,
                checkpoint,
                candidate_side,
            )
        else:
            placeholder_line = None
            insert_before = None
            placeholder_position = None
        final_indices = set(mandatory_indices)
        final_indices.update(optional_keep)
        final_size = sum(line_bytes[index] for index in final_indices)
        if placeholder_line:
            final_size += len(placeholder_line.encode("utf-8"))

    return {
        "mode": mode,
        "checkpoint": checkpoint,
        "meta_index": meta_index,
        "mandatory_bytes": mandatory_bytes,
        "target_bytes": target_bytes,
        "breadcrumb_policy": "two-ended event_msg plus pre-checkpoint turn_context breadcrumb before newest full CompactedItem.replacement_history, then response_item detail with leftover budget; physically omitted pre-checkpoint middle removed",
        "first_user": first_user,
        "candidate_count": len(breadcrumb_candidates),
        "optional_keep_count": len(optional_keep),
        "ui_candidate_count": len(ui_candidates),
        "ui_keep_count": len([index for index in optional_keep if index in ui_candidates]),
        "ui_omitted_count": len([index for index in ui_candidates if index not in optional_keep]),
        "response_candidate_count": len(response_candidates),
        "response_keep_count": len([index for index in optional_keep if index in response_candidates]),
        "response_omitted_count": len([index for index in response_candidates if index not in optional_keep]),
        "breadcrumb_omitted_count": len(breadcrumb_omitted),
        "historical_rollout_candidate_count": len(historical_indices),
        "omitted_count": len(omitted),
        "omitted_bytes": omitted_bytes,
        "placeholder_line": placeholder_line,
        "insert_before": insert_before,
        "placeholder_position": placeholder_position,
        "placeholder_language": placeholder_language,
        "keep_indices": final_indices,
        "final_size": final_size,
    }


def write_semantic_checkpoint_cut(src, dst, info, target_bytes):
    plan = build_semantic_checkpoint_plan(info, target_bytes)
    if plan is None:
        return None

    keep_indices = plan["keep_indices"]
    meta_index = plan["meta_index"]
    meta_line = info.get("session_meta_line")
    placeholder_line = plan.get("placeholder_line")
    insert_before = plan.get("insert_before")
    placeholder_written = False

    with open(src, "r", encoding="utf-8") as fin, open(dst, "w", encoding="utf-8") as fout:
        if meta_index != 0 and meta_line:
            fout.write(meta_line)
        for index, line in enumerate(fin):
            if index == meta_index and meta_index != 0:
                continue
            if placeholder_line and (not placeholder_written) and index == insert_before:
                fout.write(placeholder_line)
                placeholder_written = True
            if index in keep_indices:
                fout.write(line)
        if placeholder_line and not placeholder_written:
            fout.write(placeholder_line)

    return plan


def find_oversized(target_bytes, base=None):
    base = os.path.expanduser(base) if base else default_sessions_base()
    hits = []
    for root, _, files in os.walk(base):
        for filename in files:
            if not filename.startswith("rollout-") or not filename.endswith(".jsonl"):
                continue
            path = os.path.join(root, filename)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > target_bytes:
                hits.append((size, path))
    return sorted(hits, reverse=True)


def main():
    parser = argparse.ArgumentParser(description="Slim a Codex rollout with semantic checkpoint cut.")
    parser.add_argument("rollout", nargs="?", help="path to rollout-*.jsonl, omit with --auto")
    parser.add_argument("--auto", action="store_true", help="scan the sessions directory for oversized rollouts")
    parser.add_argument("--base", default=default_sessions_base(), help="base sessions directory for --auto; default: CODEX_HOME/sessions when CODEX_HOME is set, otherwise ~/.codex/sessions")
    parser.add_argument("--goal-size", default=DEFAULT_GOAL_SIZE, help="target rollout size, default 100MB. Plain numbers mean MiB.")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen; do not modify files")
    parser.add_argument("--output", default=None, help="write a compressed copy here instead of replacing the original")
    parser.add_argument("--backup-dir", default=None, help="where to rename-archive original; default: alongside file. Must be on the same filesystem.")
    parser.add_argument("--json", action="store_true", help="print machine-readable plan/result JSON")
    args = parser.parse_args()

    target_bytes = parse_size(args.goal_size)
    if target_bytes >= V8_MAX_STRING:
        print("warning: goal %s is above V8 ceiling %s" % (human(target_bytes), human(V8_MAX_STRING)), file=sys.stderr)

    if args.auto:
        hits = find_oversized(target_bytes, args.base)
        if not hits:
            if args.json:
                print_json(
                    {
                        "auto": True,
                        "base": os.path.expanduser(args.base),
                        "goal_bytes": target_bytes,
                        "goal_size": human(target_bytes),
                        "changed": False,
                        "dry_run": bool(args.dry_run),
                        "reason": "no_rollouts_over_goal",
                    }
                )
                return 0
            print("No rollouts over %s under %s." % (human(target_bytes), os.path.expanduser(args.base)))
            return 0
        if not args.json:
            print("Oversized rollouts, largest first:")
            for size, path in hits:
                print("  %10s  %s" % (human(size), path))
        path = hits[0][1]
        if not args.json:
            print("\nSelecting largest: %s\n" % path)
    elif args.rollout:
        path = os.path.expanduser(args.rollout)
    else:
        parser.error("provide a rollout path or use --auto")

    if not os.path.isfile(path):
        print("error: not a file: %s" % path, file=sys.stderr)
        return 2

    start = os.path.getsize(path)
    if not args.json:
        print("Rollout : %s" % path)
        print("Size    : %s  (goal %s, V8 ceiling %s)" % (human(start), human(target_bytes), human(V8_MAX_STRING)))
    if start <= target_bytes:
        if args.json:
            print_json(
                {
                    "path": path,
                    "start_bytes": start,
                    "final_bytes": start,
                    "goal_bytes": target_bytes,
                    "changed": False,
                    "dry_run": bool(args.dry_run),
                    "reason": "already_under_goal",
                }
            )
            return 0
        print("Already under goal - nothing to do.")
        return 0

    output_path = os.path.expanduser(args.output) if args.output else None
    if output_path and os.path.abspath(output_path) == os.path.abspath(path):
        print("error: --output must not be the same path as the input rollout", file=sys.stderr)
        return 2
    workdir = os.path.dirname(os.path.abspath(output_path or path))
    os.makedirs(workdir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".jsonl.tmp", dir=workdir)
    os.close(fd)

    try:
        try:
            info = scan(path)
        except UnicodeDecodeError as exc:
            print("error: rollout is not valid UTF-8; refusing to rewrite: %s" % exc, file=sys.stderr)
            return 1
        if info["bad_json"]:
            print("error: rollout contains %d invalid JSON line(s); refusing to rewrite" % info["bad_json"], file=sys.stderr)
            return 1
        if info.get("last_full_compacted", -1) < 0:
            print("error: no full compacted checkpoint with replacement_history found; semantic compression cannot proceed", file=sys.stderr)
            return 1

        try:
            plan = write_semantic_checkpoint_cut(path, tmp_path, info, target_bytes)
        except UnicodeDecodeError as exc:
            print("error: rollout is not valid UTF-8 while writing slimmed copy: %s" % exc, file=sys.stderr)
            return 1
        if plan is None:
            print("error: failed to build semantic checkpoint plan", file=sys.stderr)
            return 1

        final = os.path.getsize(tmp_path)
        result = {
            "path": path,
            "auto": bool(args.auto),
            "auto_base": os.path.expanduser(args.base) if args.auto else "",
            "output_path": output_path or path,
            "start_bytes": start,
            "start_size": human(start),
            "final_bytes": final,
            "final_size": human(final),
            "goal_bytes": target_bytes,
            "goal_size": human(target_bytes),
            "v8_ceiling_bytes": V8_MAX_STRING,
            "checkpoint_line": plan["checkpoint"] + 1,
            "mandatory_bytes": plan["mandatory_bytes"],
            "mandatory_size": human(plan["mandatory_bytes"]),
            "historical_breadcrumb_items_kept": plan["optional_keep_count"],
            "historical_breadcrumb_items_candidates": plan["candidate_count"],
            "historical_breadcrumb_items_omitted": plan["breadcrumb_omitted_count"],
            "historical_rollout_lines_candidates": plan["historical_rollout_candidate_count"],
            "historical_rollout_lines_omitted": plan["omitted_count"],
            "historical_ui_items_kept": plan["ui_keep_count"],
            "historical_ui_items_candidates": plan["ui_candidate_count"],
            "historical_ui_items_omitted": plan["ui_omitted_count"],
            "historical_response_items_kept": plan["response_keep_count"],
            "historical_response_items_candidates": plan["response_candidate_count"],
            "historical_response_items_omitted": plan["response_omitted_count"],
            "omitted_bytes": plan["omitted_bytes"],
            "omitted_size": human(plan["omitted_bytes"]),
            "mode": plan["mode"],
            "breadcrumb_policy": plan["breadcrumb_policy"],
            "placeholder_type": "event_msg.synthetic_maintenance_turn.user_message_agent_message",
            "placeholder_line_count": 4 if plan["placeholder_line"] else 0,
            "placeholder_position": plan["placeholder_position"],
            "placeholder_language": plan["placeholder_language"],
            "dry_run": bool(args.dry_run),
            "changed": not bool(args.dry_run),
            "backup_path": None,
        }
        if args.json and args.dry_run:
            print_json(result)
        elif not args.json:
            print(
                "  - %-16s newest full CompactedItem.replacement_history line %d: %s -> %s"
                % ("checkpoint-cut", plan["checkpoint"] + 1, human(start), human(final))
            )
            print(
                "    mandatory=%s, historical UI items kept=%d/%d, response_items kept=%d/%d, physically omitted pre-checkpoint lines=%d (%s), mode=%s"
                % (
                    human(plan["mandatory_bytes"]),
                    plan["ui_keep_count"],
                    plan["ui_candidate_count"],
                    plan["response_keep_count"],
                    plan["response_candidate_count"],
                    plan["omitted_count"],
                    human(plan["omitted_bytes"]),
                    plan["mode"],
                )
            )
            print("    breadcrumb_policy=%s" % plan["breadcrumb_policy"])

            print("\nResult  : %s -> %s  (applied: checkpoint-cut)" % (human(start), human(final)))
        if final > target_bytes:
            print("warning: result is above goal %s because the mandatory checkpoint segment may be larger than the goal." % human(target_bytes), file=sys.stderr)
        if final >= V8_MAX_STRING:
            print("ERROR: still above V8 ceiling; inspect manually.", file=sys.stderr)
            return 1

        if args.dry_run:
            if not args.json:
                print("\n[dry-run] no files modified. Re-run without --dry-run to apply.")
            return 0

        if output_path:
            os.replace(tmp_path, output_path)
            tmp_path = None
            if args.json:
                result["changed"] = True
                print_json(result)
            else:
                print("\nWrote slimmed copy -> %s" % output_path)
                print("Original left unchanged -> %s" % path)
            return 0

        backup_dir = os.path.abspath(os.path.expanduser(args.backup_dir)) if args.backup_dir else workdir
        os.makedirs(backup_dir, exist_ok=True)
        try:
            if os.stat(os.path.dirname(os.path.abspath(path))).st_dev != os.stat(backup_dir).st_dev:
                print(
                    "error: backup directory is on a different filesystem; refusing to copy the full rollout. "
                    "Use a backup directory on the same filesystem, or use --output to write a compressed copy.",
                    file=sys.stderr,
                )
                return 2
        except OSError as exc:
            print("error: could not validate backup directory filesystem: %s" % exc, file=sys.stderr)
            return 2
        backup = os.path.join(backup_dir, os.path.basename(path) + ".orig")
        n = 1
        while os.path.exists(backup):
            backup = os.path.join(backup_dir, os.path.basename(path) + ".orig.%d" % n)
            n += 1
        original_mode = stat.S_IMODE(os.stat(path).st_mode)
        os.chmod(tmp_path, original_mode)
        try:
            os.replace(path, backup)
        except OSError as exc:
            print("error: failed to archive original by rename: %s" % exc, file=sys.stderr)
            return 1
        result["backup_path"] = backup
        result["backup_method"] = "rename"
        try:
            os.replace(tmp_path, path)
            tmp_path = None
        except OSError as exc:
            try:
                os.replace(backup, path)
            except OSError as restore_exc:
                print(
                    "error: failed to install slimmed rollout after original was archived: %s; "
                    "also failed to restore backup: %s" % (exc, restore_exc),
                    file=sys.stderr,
                )
                return 1
            print(
                "error: failed to install slimmed rollout: %s; original was restored from backup" % exc,
                file=sys.stderr,
            )
            return 1

        if args.json:
            result["changed"] = True
            print_json(result)
        else:
            print("\nArchived original by rename -> %s" % backup)
            print("Slimmed file in place -> %s" % path)
            print("\nNext: run verify_rollout.py, then relaunch or resume Codex to test.")
            print("To undo: mv '%s' '%s'" % (backup, path))
        return 0
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
