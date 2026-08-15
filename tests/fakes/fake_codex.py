#!/usr/bin/env python3
"""Synthetic Codex CLI used by engineering-controller tests.

It implements only the command surface the controller uses. No network or real
Codex quota is consumed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


THREAD_ID = "0199fake0-0000-7000-8000-engineeringctrl"


def option_value(args: list[str], option: str) -> str | None:
    try:
        index = args.index(option)
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def load_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"worker": 0, "reviewer": 0, "resume": 0}
    return json.loads(path.read_text(encoding="utf-8"))


def save_counts(path: Path, counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts), encoding="utf-8")


def worker_completed(summary: str = "Synthetic task completed.") -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "summary": summary,
        "checks": [{"name": "synthetic", "status": "PASS", "detail": "Synthetic validation passed."}],
        "gate": None,
        "failure": None,
    }


def worker_gate() -> dict[str, Any]:
    return {
        "status": "GATE_REQUIRED",
        "summary": "Synthetic review gate.",
        "checks": [{"name": "synthetic", "status": "PASS", "detail": "Gate fixture ready."}],
        "gate": {
            "type": "SYNTHETIC_DIVERGENCE",
            "key": "synthetic-divergence",
            "reason": "A synthetic divergence needs independent review.",
            "proposed_action": "Apply the localized synthetic correction inside the task scope.",
            "risk_flags": [],
            "evidence": [{"kind": "TEST", "ref": "TASK.md", "note": "Synthetic gate evidence."}],
        },
        "failure": None,
    }


def human_gate() -> dict[str, Any]:
    value = worker_gate()
    value["gate"] = dict(value["gate"])
    value["gate"]["type"] = "DANGEROUS_GIT"
    value["gate"]["key"] = "dangerous-git"
    value["gate"]["proposed_action"] = "Run git push --force origin main"
    return value


def reviewer(decision: str) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason": f"Synthetic reviewer returned {decision}.",
        "instructions": "Apply only the localized synthetic correction." if decision == "REVISE" else "",
        "risk_flags": [],
    }


def emit_jsonl(command_event: str | None = None) -> None:
    print(json.dumps({"type": "thread.started", "thread_id": THREAD_ID}))
    print(json.dumps({"type": "turn.started"}))
    if command_event:
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "cmd_1",
                        "type": "command_execution",
                        "command": command_event,
                        "status": "completed",
                        "exit_code": 0,
                    },
                }
            )
        )
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))


def main() -> int:
    args = sys.argv[1:]
    scenario = os.environ.get("EC_FAKE_SCENARIO", "completed")
    counts_path = Path(os.environ["EC_FAKE_STATE"])
    counts = load_counts(counts_path)

    output_raw = option_value(args, "--output-last-message")
    project_raw = option_value(args, "-C")
    if not output_raw or not project_raw:
        print("fake_codex: required controller options missing", file=sys.stderr)
        return 64

    output_path = Path(output_raw)
    project_root = Path(project_raw)
    is_reviewer = option_value(args, "--sandbox") == "read-only"
    is_resume = "resume" in args

    if scenario == "timeout":
        time.sleep(float(os.environ.get("EC_FAKE_SLEEP", "5")))

    if is_reviewer:
        counts["reviewer"] += 1
        save_counts(counts_path, counts)
        if scenario == "reviewer_exit":
            emit_jsonl()
            return 9
        if scenario == "invalid_reviewer_json":
            output_path.write_text("{invalid reviewer", encoding="utf-8")
            emit_jsonl()
            return 0
        decision = "APPROVE" if scenario == "approve" else "REVISE"
        output_path.write_text(json.dumps(reviewer(decision)), encoding="utf-8")
        emit_jsonl()
        return 0

    counts["worker"] += 1
    if is_resume:
        counts["resume"] += 1
    save_counts(counts_path, counts)

    if scenario == "worker_exit":
        emit_jsonl()
        return 7

    if scenario == "invalid_worker_json":
        output_path.write_text("not json", encoding="utf-8")
        emit_jsonl()
        return 0

    command_event = None
    if scenario == "command_violation":
        command_event = "git commit -am synthetic"
    elif scenario == "project_command_violation":
        command_event = "synthetic-custom-forbidden --apply"

    if scenario == "secret_change":
        (project_root / ".env").write_text("TOKEN=synthetic-not-a-real-secret\n", encoding="utf-8")
        payload = worker_completed()
    elif scenario == "policy_change":
        (project_root / ".engineering-controller-policy.json").write_text("{}\n", encoding="utf-8")
        payload = worker_completed()
    elif scenario == "completed_failed_check":
        (project_root / "result.txt").write_text("synthetic result\n", encoding="utf-8")
        payload = worker_completed()
        payload["checks"][0]["status"] = "FAIL"
        payload["checks"][0]["detail"] = "Synthetic check deliberately failed."
    elif scenario == "human_command":
        payload = human_gate()
    elif scenario == "gate_loop":
        payload = worker_gate()
    elif scenario in {"revise", "approve", "invalid_reviewer_json", "reviewer_exit"}:
        if is_resume:
            (project_root / "result.txt").write_text("synthetic result\n", encoding="utf-8")
            payload = worker_completed()
        else:
            (project_root / "draft.txt").write_text("synthetic draft\n", encoding="utf-8")
            payload = worker_gate()
    else:
        (project_root / "result.txt").write_text("synthetic result\n", encoding="utf-8")
        payload = worker_completed()

    output_path.write_text(json.dumps(payload), encoding="utf-8")
    emit_jsonl(command_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
