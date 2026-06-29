#!/usr/bin/env python3
"""
confirm_session_with_codex_cli.py - read a compressed session through Codex CLI.

This is a post-repair smoke check that uses `codex app-server --stdio` and the
read-only `thread/read` API with `includeTurns=true`. It does not send
`thread/resume`, `turn/start`, or any prompt, so it should not append a turn or
consume model usage.
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

SYNTHETIC_TURN_ID_PREFIX = "codex-session-compress-elision-"
SYNTHETIC_TEXT_MARKER = "codex-session-compress"


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def send_json(proc, obj):
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()


def stream_reader(stream, output_queue):
    try:
        for line in stream:
            output_queue.put(line)
    finally:
        output_queue.put(None)


def start_stream_reader(stream):
    output_queue = queue.Queue()
    thread = threading.Thread(target=stream_reader, args=(stream, output_queue), daemon=True)
    thread.start()
    return output_queue


def drain_queue(output_queue):
    items = []
    while output_queue is not None:
        try:
            item = output_queue.get_nowait()
        except queue.Empty:
            break
        if item is not None:
            items.append(item)
    return items


def read_response(stdout_queue, proc, request_id, timeout):
    deadline = time.time() + timeout
    notifications = []
    while time.time() < deadline:
        remaining = max(0.0, deadline - time.time())
        try:
            line = stdout_queue.get(timeout=min(0.5, remaining))
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            notifications.append({"unparsed": text[:500]})
            continue
        if obj.get("id") == request_id:
            return obj, notifications
        if "method" in obj:
            notifications.append({"method": obj.get("method")})
    return None, notifications


def walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)


def item_text(item):
    return "\n".join(walk_strings(item))


def iter_turn_items(thread):
    for turn_index, turn in enumerate(thread.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        for item_index, item in enumerate(turn.get("items") or []):
            if isinstance(item, dict):
                yield turn_index, item_index, item


def analyze_thread(thread):
    turns = thread.get("turns") or []
    items = list(iter_turn_items(thread))
    synthetic_user_items = []
    synthetic_agent_items = []
    marker_items = []
    user_turns = set()
    agent_turns = set()
    for turn_index, item_index, item in items:
        text = item_text(item)
        item_type = item.get("type")
        client_id = item.get("clientId") or item.get("client_id") or ""
        hit = SYNTHETIC_TEXT_MARKER in text or str(client_id).startswith(SYNTHETIC_TURN_ID_PREFIX)
        if hit:
            marker_items.append((turn_index, item_index, item))
        if item_type == "userMessage" and str(client_id).startswith(SYNTHETIC_TURN_ID_PREFIX):
            synthetic_user_items.append((turn_index, item_index, item))
            user_turns.add(turn_index)
        if item_type == "agentMessage" and SYNTHETIC_TEXT_MARKER in text:
            synthetic_agent_items.append((turn_index, item_index, item))
            agent_turns.add(turn_index)

    user_preview = ""
    if synthetic_user_items:
        user_preview = item_text(synthetic_user_items[0][2])[:500]
    agent_preview = ""
    if synthetic_agent_items:
        agent_preview = item_text(synthetic_agent_items[0][2])[:500]
    synthetic_turn_indexes = sorted(user_turns & agent_turns)

    return {
        "thread_id": thread.get("id"),
        "session_id": thread.get("sessionId"),
        "name": thread.get("name"),
        "turn_count": len(turns),
        "item_count": len(items),
        "synthetic_marker_item_count": len(marker_items),
        "synthetic_user_message_count": len(synthetic_user_items),
        "synthetic_agent_message_count": len(synthetic_agent_items),
        "synthetic_user_found": bool(synthetic_user_items),
        "synthetic_agent_found": bool(synthetic_agent_items),
        "synthetic_turn_found": bool(synthetic_turn_indexes),
        "synthetic_turn_indexes": synthetic_turn_indexes[:20],
        "synthetic_user_preview": user_preview,
        "synthetic_agent_preview": agent_preview,
    }


def terminate(proc):
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Confirm a Codex session by reading it through `codex app-server --stdio`.")
    parser.add_argument("session_id", help="Codex session UUID")
    parser.add_argument("--codex-bin", default=None, help="Codex CLI path. Default: first `codex` on PATH.")
    parser.add_argument("--codex-home", default=None, help="Codex home to use for app-server confirmation. Sets CODEX_HOME for the child process.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for each app-server response. Default: 120.")
    parser.add_argument("--require-synthetic-marker", action="store_true", help="Require the compressed synthetic user+agent marker turn to appear in the same reconstructed thread turn.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    codex = args.codex_bin or shutil.which("codex")
    if not codex:
        result = {"ok": False, "error": "codex CLI not found on PATH"}
        if args.json:
            print_json(result)
        else:
            print("error: codex CLI not found on PATH", file=sys.stderr)
        return 127

    env = os.environ.copy()
    codex_home = os.path.abspath(os.path.expanduser(args.codex_home)) if args.codex_home else ""
    if codex_home:
        env["CODEX_HOME"] = codex_home
    proc = subprocess.Popen(
        [codex, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    stdout_queue = start_stream_reader(proc.stdout)
    stderr_queue = start_stream_reader(proc.stderr)
    result = {
        "ok": False,
        "codex_bin": codex,
        "codex_home": codex_home or env.get("CODEX_HOME", ""),
        "session_id": args.session_id,
        "thread_read_ok": False,
        "require_synthetic_marker": bool(args.require_synthetic_marker),
    }
    try:
        init_id = str(uuid.uuid4())
        send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "codex-session-compress-confirm",
                        "title": "codex-session-compress confirm",
                        "version": "0",
                    },
                    "capabilities": {
                        "experimentalApi": True,
                        "requestAttestation": False,
                        "optOutNotificationMethods": [],
                        "mcpServerOpenaiFormElicitation": False,
                    },
                },
            },
        )
        init, init_notifications = read_response(stdout_queue, proc, init_id, args.timeout)
        result["initialize_notifications"] = init_notifications
        if not init or "error" in init:
            result["error"] = "initialize failed"
            result["initialize_response"] = init
            return finish(result, proc, args.json, 1, stderr_queue)

        send_json(proc, {"jsonrpc": "2.0", "method": "initialized", "params": {}})

        read_id = str(uuid.uuid4())
        send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": read_id,
                "method": "thread/read",
                "params": {"threadId": args.session_id, "includeTurns": True},
            },
        )
        response, notifications = read_response(stdout_queue, proc, read_id, args.timeout)
        result["thread_read_notifications"] = notifications
        if not response or "error" in response:
            result["error"] = "thread/read failed"
            result["thread_read_response"] = response
            return finish(result, proc, args.json, 1, stderr_queue)

        thread = ((response.get("result") or {}).get("thread") or {})
        analysis = analyze_thread(thread)
        result.update(analysis)
        result["thread_read_ok"] = bool(thread)
        marker_ok = (not args.require_synthetic_marker) or bool(analysis["synthetic_turn_found"])
        result["ok"] = bool(thread) and marker_ok
        if args.require_synthetic_marker and not analysis["synthetic_turn_found"]:
            result["error"] = "synthetic compression marker user+agent pair was not found in the same Codex thread/read turn"
        return finish(result, proc, args.json, 0 if result["ok"] else 1, stderr_queue)
    except BrokenPipeError as exc:
        result["error"] = "codex app-server pipe closed: %s" % exc
        return finish(result, proc, args.json, 1, stderr_queue)
    finally:
        terminate(proc)


def finish(result, proc, json_mode, rc, stderr_queue=None):
    terminate(proc)
    stderr = "".join(drain_queue(stderr_queue)) if stderr_queue is not None else ""
    if stderr:
        result["stderr"] = stderr[-4000:]
    if json_mode:
        print_json(result)
    else:
        if result.get("ok"):
            print("Codex CLI confirmation succeeded.")
            print("  thread/read: ok")
            print(
                "  synthetic marker: user=%s agent=%s same_turn=%s"
                % (
                    result.get("synthetic_user_found"),
                    result.get("synthetic_agent_found"),
                    result.get("synthetic_turn_found"),
                )
            )
            print("  turns/items: %s/%s" % (result.get("turn_count"), result.get("item_count")))
        else:
            print("Codex CLI confirmation failed: %s" % result.get("error", "unknown error"), file=sys.stderr)
            if result.get("thread_read_ok"):
                print(
                    "  synthetic marker: user=%s agent=%s same_turn=%s"
                    % (
                        result.get("synthetic_user_found"),
                        result.get("synthetic_agent_found"),
                        result.get("synthetic_turn_found"),
                    ),
                    file=sys.stderr,
                )
    return rc


if __name__ == "__main__":
    sys.exit(main())
