#!/usr/bin/env python3
"""
verify_rollout.py - confirm that a Codex rollout is safe to load and resume.

Checks:
  * every non-empty line is valid JSON
  * every parsed RolloutLine has a top-level timestamp
  * a session_meta record with id exists
  * compacted checkpoint presence when required
  * full compacted checkpoint presence when required
  * no embedded image parts remain, unless semantic retained images are allowed
  * synthetic maintenance marker placement and shape in semantic mode
  * active suffix function_call/function_call_output pairing details in
    semantic mode
  * file size is below the V8 single-string ceiling

Exit code 0 means safe to load; exit code 1 means one or more required checks
failed; exit code 2 means command usage or file errors.
"""

import argparse
from collections import Counter
import json
import os
import sys

V8_MAX_STRING = 536_870_888
SAFE_MARGIN_MB = 480
SYNTHETIC_TURN_ID_PREFIX = "codex-session-compress-elision-"


def human(value):
    n = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return "%s%s" % (("%.1f" % n) if unit != "B" else str(int(n)), unit)
        n /= 1024


def count_images(obj):
    count = 0
    if isinstance(obj, dict):
        kind = obj.get("type")
        if kind == "input_image":
            return 1
        elif kind == "image" and isinstance(obj.get("data"), str):
            return 1
        for value in obj.values():
            count += count_images(value)
    elif isinstance(obj, list):
        for item in obj:
            count += count_images(item)
    elif isinstance(obj, str):
        if obj.startswith("data:image") and ";base64," in obj:
            count += 1
    return count


def payload_call_id(payload):
    call_id = payload.get("call_id")
    return call_id if isinstance(call_id, str) and call_id else "<missing-call-id>"


def counter_delta(left, right):
    missing = left - right
    extra = right - left
    parts = []
    if missing:
        parts.append("missing outputs for " + ", ".join("%s x%d" % (key, count) for key, count in missing.most_common(5)))
    if extra:
        parts.append("orphan outputs for " + ", ".join("%s x%d" % (key, count) for key, count in extra.most_common(5)))
    return "; ".join(parts) if parts else "call_id pairs matched"


def synthetic_turn_shape_errors(event_payloads_by_line):
    errors = []
    synthetic_lines = set()
    starts = []
    for line_index, payload in sorted(event_payloads_by_line.items()):
        event_type = payload.get("type")
        if event_type in ("task_started", "task_complete"):
            turn_id = payload.get("turn_id")
            if isinstance(turn_id, str) and turn_id.startswith(SYNTHETIC_TURN_ID_PREFIX):
                synthetic_lines.add(line_index)
                if event_type == "task_started":
                    starts.append((line_index, turn_id))
        elif event_type == "user_message":
            client_id = payload.get("client_id")
            if isinstance(client_id, str) and client_id.startswith(SYNTHETIC_TURN_ID_PREFIX):
                synthetic_lines.add(line_index)

    covered = set()
    for start_line, turn_id in starts:
        expected = [
            (start_line, "task_started"),
            (start_line + 1, "user_message"),
            (start_line + 2, "agent_message"),
            (start_line + 3, "task_complete"),
        ]
        turn_errors = []
        for line_index, expected_type in expected:
            payload = event_payloads_by_line.get(line_index)
            if not payload:
                turn_errors.append("line %d is missing" % line_index)
                continue
            actual_type = payload.get("type")
            if actual_type != expected_type:
                turn_errors.append("line %d expected %s, got %s" % (line_index, expected_type, actual_type))
                continue
            if expected_type == "user_message" and payload.get("client_id") != turn_id:
                turn_errors.append("line %d user_message.client_id does not match %s" % (line_index, turn_id))
            elif expected_type == "task_complete" and payload.get("turn_id") != turn_id:
                turn_errors.append("line %d task_complete.turn_id does not match %s" % (line_index, turn_id))
            elif expected_type == "agent_message":
                message = payload.get("message")
                if not isinstance(message, str) or "codex-session-compress" not in message:
                    turn_errors.append("line %d agent_message lacks codex-session-compress marker" % line_index)
        if turn_errors:
            errors.append("turn %s at line %d: %s" % (turn_id, start_line, "; ".join(turn_errors)))
        else:
            covered.update(line_index for line_index, _ in expected)

    for line_index in sorted(synthetic_lines - covered):
        errors.append("synthetic marker at line %d is not part of a valid four-event maintenance turn" % line_index)
    return errors


def main():
    parser = argparse.ArgumentParser(description="Verify a Codex rollout is safe to load/resume.")
    parser.add_argument("rollout", help="path to rollout-*.jsonl")
    parser.add_argument("--limit-mb", type=float, default=None, help="size ceiling in MB; default: V8 max string")
    parser.add_argument("--require-compacted", action="store_true", help="fail if no compacted checkpoint remains")
    parser.add_argument("--require-full-compacted", action="store_true", help="fail if no compacted checkpoint with replacement_history remains")
    parser.add_argument("--semantic-checkpoint", action="store_true", help="verify active suffix after newest full compacted checkpoint; implies --require-full-compacted")
    parser.add_argument(
        "--allow-active-images",
        action="store_true",
        help="allow embedded images retained by semantic compression, including full checkpoint images, active suffix images, and historical breadcrumb images",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable verification details.")
    args = parser.parse_args()
    if args.semantic_checkpoint:
        args.require_compacted = True
        args.require_full_compacted = True

    path = os.path.expanduser(args.rollout)
    if not os.path.isfile(path):
        print("error: not a file: %s" % path, file=sys.stderr)
        return 2

    ceiling = int(args.limit_mb * 1024 * 1024) if args.limit_mb else V8_MAX_STRING
    size = os.path.getsize(path)

    lines = 0
    bad = 0
    function_calls = 0
    function_outputs = 0
    images = 0
    active_images = 0
    checkpoint_images = 0
    historical_breadcrumb_images = 0
    disallowed_images = 0
    image_records = []
    compacted_count = 0
    last_compacted_line = None
    full_compacted_count = 0
    last_full_compacted_line = None
    max_line = 0
    meta_id = None
    first_is_meta = False
    active_function_calls = 0
    active_function_outputs = 0
    function_call_ids = Counter()
    function_output_ids = Counter()
    active_function_call_ids = Counter()
    active_function_output_ids = Counter()
    bad_encoding = None
    missing_timestamp_lines = []
    task_event_lines = []
    synthetic_marker_lines = []
    synthetic_user_message_lines = []
    synthetic_agent_message_lines = []
    event_payloads_by_line = {}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                max_line = max(max_line, len(line.encode("utf-8")))
                text = line.strip()
                if not text:
                    continue
                lines += 1
                try:
                    obj = json.loads(text)
                except Exception:
                    bad += 1
                    continue
                if not isinstance(obj.get("timestamp"), str) or not obj.get("timestamp"):
                    missing_timestamp_lines.append(index)
                kind = obj.get("type")
                if kind == "event_msg":
                    payload = obj.get("payload") or {}
                    event_payloads_by_line[index] = payload
                    event_type = payload.get("type")
                    if event_type in ("task_started", "task_complete"):
                        task_event_lines.append(index)
                        turn_id = payload.get("turn_id")
                        if isinstance(turn_id, str) and turn_id.startswith(SYNTHETIC_TURN_ID_PREFIX):
                            synthetic_marker_lines.append(index)
                    elif event_type == "user_message":
                        client_id = payload.get("client_id")
                        if isinstance(client_id, str) and client_id.startswith(SYNTHETIC_TURN_ID_PREFIX):
                            synthetic_marker_lines.append(index)
                            synthetic_user_message_lines.append(index)
                    elif event_type == "agent_message":
                        message = payload.get("message")
                        if isinstance(message, str) and "codex-session-compress" in message:
                            synthetic_agent_message_lines.append(index)
                if kind == "session_meta" and meta_id is None:
                    meta_id = (obj.get("payload") or {}).get("id")
                    first_is_meta = index == 0
                elif kind == "compacted":
                    compacted_count += 1
                    last_compacted_line = index
                    payload = obj.get("payload") or {}
                    if isinstance(payload.get("replacement_history"), list):
                        full_compacted_count += 1
                        last_full_compacted_line = index
                        active_function_calls = 0
                        active_function_outputs = 0
                        active_images = 0
                        active_function_call_ids = Counter()
                        active_function_output_ids = Counter()
                elif kind == "response_item":
                    payload = obj.get("payload") or {}
                    payload_type = payload.get("type")
                    if payload_type == "function_call":
                        function_calls += 1
                        call_id = payload_call_id(payload)
                        function_call_ids[call_id] += 1
                        if last_full_compacted_line is not None and index > last_full_compacted_line:
                            active_function_calls += 1
                            active_function_call_ids[call_id] += 1
                    elif payload_type == "function_call_output":
                        function_outputs += 1
                        call_id = payload_call_id(payload)
                        function_output_ids[call_id] += 1
                        if last_full_compacted_line is not None and index > last_full_compacted_line:
                            active_function_outputs += 1
                            active_function_output_ids[call_id] += 1
                line_images = count_images(obj)
                images += line_images
                if line_images:
                    image_records.append((index, kind, line_images))
                if last_full_compacted_line is not None and index > last_full_compacted_line:
                    active_images += line_images
    except UnicodeDecodeError as exc:
        bad_encoding = str(exc)

    if images and args.allow_active_images:
        for line_index, kind, line_images in image_records:
            if last_full_compacted_line is not None and line_index > last_full_compacted_line:
                continue
            if last_full_compacted_line is not None and line_index == last_full_compacted_line and kind == "compacted":
                checkpoint_images += line_images
                continue
            if last_full_compacted_line is not None and line_index < last_full_compacted_line and kind in ("event_msg", "turn_context", "response_item"):
                historical_breadcrumb_images += line_images
                continue
            disallowed_images += line_images
    image_check_passed = images == 0 or (args.allow_active_images and disallowed_images == 0)
    image_detail = "%d image part(s) remain" % images if images else "0"
    if args.allow_active_images and images:
        image_detail = "%d total image part(s); %d in newest full checkpoint, %d after newest full checkpoint, %d in retained historical breadcrumbs, %d disallowed" % (
            images,
            checkpoint_images,
            active_images,
            historical_breadcrumb_images,
            disallowed_images,
        )

    checks = [
        ("valid UTF-8", bad_encoding is None, bad_encoding or "OK"),
        ("valid JSON on every line", bad == 0, "%d unparseable line(s)" % bad if bad else "%d lines OK" % lines),
        (
            "Codex RolloutLine timestamp present",
            not missing_timestamp_lines,
            "%d missing timestamp line(s)%s"
            % (
                len(missing_timestamp_lines),
                ": " + ", ".join(str(line) for line in missing_timestamp_lines[:5]) if missing_timestamp_lines else "",
            ),
        ),
        ("session_meta id present", bool(meta_id), meta_id or "MISSING - loader will fail"),
        (
            "compacted checkpoint present when required",
            (not args.require_compacted) or compacted_count > 0,
            "%d compacted record(s); newest line %s" % (compacted_count, last_compacted_line if last_compacted_line is not None else "n/a"),
        ),
        (
            "full compacted checkpoint present when required",
            (not args.require_full_compacted) or full_compacted_count > 0,
            "%d full compacted record(s); newest line %s"
            % (full_compacted_count, last_full_compacted_line if last_full_compacted_line is not None else "n/a"),
        ),
        ("embedded images policy", image_check_passed, image_detail),
        ("under size ceiling", size < ceiling, "%s / ceiling %s" % (human(size), human(ceiling))),
    ]
    pairing_ok = function_call_ids == function_output_ids
    active_pairing_ok = active_function_call_ids == active_function_output_ids
    if args.semantic_checkpoint:
        synthetic_shape_errors = synthetic_turn_shape_errors(event_payloads_by_line)
        synthetic_marker_before_checkpoint = (
            not synthetic_marker_lines
            or (
                last_full_compacted_line is not None
                and all(line_index < last_full_compacted_line for line_index in synthetic_marker_lines)
            )
        )
        checks.append(
            (
                "synthetic compression marker stays before newest full checkpoint",
                synthetic_marker_before_checkpoint,
                "%d marker event(s), %d synthetic user_message event(s); newest full compacted line %s"
                % (
                    len(synthetic_marker_lines),
                    len(synthetic_user_message_lines),
                    last_full_compacted_line if last_full_compacted_line is not None else "n/a",
                ),
            )
        )
        checks.append(
            (
                "synthetic maintenance turn shape",
                not synthetic_shape_errors,
                "OK"
                if not synthetic_shape_errors
                else "%d issue(s): %s" % (len(synthetic_shape_errors), "; ".join(synthetic_shape_errors[:3])),
            )
        )
        checks.append(
            (
                "active suffix function_call/function_call_output normalization",
                True,
                "%d / %d after newest full compacted; %s"
                % (
                    active_function_calls,
                    active_function_outputs,
                    "matched"
                    if active_pairing_ok
                    else "Codex normalize_history will repair before model input: "
                    + counter_delta(active_function_call_ids, active_function_output_ids),
                ),
            )
        )

    ok = all(passed for _, passed, _ in checks)
    if args.json:
        result = {
            "path": path,
            "size_bytes": size,
            "size": human(size),
            "v8_ceiling_bytes": V8_MAX_STRING,
            "lines": lines,
            "max_line_bytes": max_line,
            "missing_timestamp_lines": missing_timestamp_lines[:20],
            "task_event_lines": task_event_lines[:20],
            "synthetic_marker_lines": synthetic_marker_lines[:20],
            "synthetic_user_message_lines": synthetic_user_message_lines[:20],
            "synthetic_agent_message_lines": synthetic_agent_message_lines[:20],
            "synthetic_turn_shape_errors": synthetic_turn_shape_errors(event_payloads_by_line)[:20],
            "session_meta_id": meta_id,
            "first_is_meta": first_is_meta,
            "compacted_count": compacted_count,
            "last_compacted_line": last_compacted_line,
            "full_compacted_count": full_compacted_count,
            "last_full_compacted_line": last_full_compacted_line,
            "function_calls": function_calls,
            "function_outputs": function_outputs,
            "active_function_calls": active_function_calls,
            "active_function_outputs": active_function_outputs,
            "active_call_pairing_ok": active_pairing_ok,
            "active_call_pairing_detail": counter_delta(active_function_call_ids, active_function_output_ids),
            "images": {
                "total": images,
                "checkpoint": checkpoint_images,
                "active_suffix": active_images,
                "historical_breadcrumb": historical_breadcrumb_images,
                "disallowed": disallowed_images,
            },
            "checks": [
                {"name": name, "passed": bool(passed), "detail": detail}
                for name, passed, detail in checks
            ],
            "verdict": "safe to load" if ok else "NOT safe",
            "ok": ok,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print("File            : %s" % path)
    print("Size            : %s  (V8 ceiling %s)" % (human(size), human(V8_MAX_STRING)))
    print("Lines           : %d  (max line %s)" % (lines, human(max_line)))
    print("session_meta id : %s  (first line: %s)" % (meta_id, first_is_meta))
    print("compacted       : %d  (newest line: %s)" % (compacted_count, last_compacted_line if last_compacted_line is not None else "n/a"))
    print("full compacted  : %d  (newest line: %s)" % (full_compacted_count, last_full_compacted_line if last_full_compacted_line is not None else "n/a"))
    print(
        "function_call / function_call_output : %d / %d  (%s)"
        % (
            function_calls,
            function_outputs,
            "matched" if pairing_ok else counter_delta(function_call_ids, function_output_ids),
        )
    )
    if args.semantic_checkpoint:
        print(
            "active suffix function_call / function_call_output : %d / %d  (%s)"
            % (
                active_function_calls,
                active_function_outputs,
                "matched" if active_pairing_ok else counter_delta(active_function_call_ids, active_function_output_ids),
            )
        )
    print()

    for name, passed, detail in checks:
        print("  [%s] %s: %s" % ("PASS" if passed else "FAIL", name, detail))

    if ok and size > SAFE_MARGIN_MB * 1024 * 1024:
        print("\n  note: file is under the ceiling but still large; consider repairing with a lower --goal-size.")

    print("\nVERDICT: %s" % ("safe to load" if ok else "NOT safe - address the FAIL(s) above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
