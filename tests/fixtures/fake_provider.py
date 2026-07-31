"""Finite fake OpenCode/Codex CLI used only by offline integration tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    provider, *arguments = sys.argv[1:]
    if arguments == ["--version"]:
        print("codex-cli 0.146.0-alpha.3.1" if provider == "codex" else "opencode 1.2.3")
        return 0
    if arguments == ["--help"]:
        print("exec --ask-for-approval" if provider == "codex" else "run")
        return 0
    if arguments in (["exec", "--help"], ["run", "--help"]):
        print(
            "--json --sandbox --ephemeral --ignore-user-config stdin read-only workspace-write"
            if provider == "codex"
            else "run --format json --model stdin"
        )
        return 0

    prompt = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    request = prompt["request"]
    role = request["role"]
    model = arguments[arguments.index("--model") + 1] if "--model" in arguments else None
    sandbox = arguments[arguments.index("--sandbox") + 1] if "--sandbox" in arguments else None
    record_path = os.environ.get("FAKE_PROVIDER_RECORD")
    if record_path:
        Path(record_path).write_text(
            json.dumps(
                {
                    "arguments": arguments,
                    "cwd": str(Path.cwd()),
                    "environment_keys": sorted(os.environ),
                    "sandbox": sandbox,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if sandbox == "workspace-write":
        Path("repair-mode-seen.txt").write_text("fake repair evidence", encoding="utf-8")
    elif provider == "codex" and "workspace-write" in arguments:
        Path("unexpected-review-write.txt").write_text("unsafe", encoding="utf-8")

    payload: dict[str, object]
    if role == "BUILDER":
        payload = {
            "role": role,
            "implementation_summary": "Fake builder claim; not locally verified.",
            "files_inspected": [],
            "files_claimed_changed": ["src/fake.py"],
            "commands_claimed_run": [],
        }
        adapter = "opencode.builder"
    elif role == "REVIEWER":
        payload = {
            "role": role,
            "review": {
                "schema_version": 1,
                "verdict": "APPROVED",
                "summary": "Fake reviewer claim; not an ApprovalGate.",
                "findings": [],
            },
            "files_inspected": [],
        }
        adapter = "codex.reviewer"
    else:
        payload = {
            "role": role,
            "repair_summary": "Fake authorized repair claim; not locally verified.",
            "files_inspected": [],
            "files_claimed_changed": ["repair-mode-seen.txt"],
            "commands_claimed_run": [],
            "addressed_finding_references": [],
        }
        adapter = "codex.repairer"
    response = {
        "schema_version": 1,
        "invocation_id": request["invocation_id"],
        "run_id": request["run_id"],
        "work_package_id": request["work_package_id"],
        "attempt_id": request["attempt_id"],
        "attempt_number": request["attempt_number"],
        "role": role,
        "expected_response_schema_version": request["expected_response_schema_version"],
        "status": "COMPLETED",
        "started_at": "2026-07-30T12:00:00Z",
        "completed_at": "2026-07-30T12:00:01Z",
        "duration_ms": 1000,
        "summary": "Finite fake provider completion",
        "public_text": os.environ.get("API_TOKEN", "Provider claims only."),
        "structured_parse_status": "PARSED",
        "payload": payload,
        "diagnostics": [],
        "artifacts": [],
        "usage": None,
        "identity": {
            "provider_id": provider,
            "adapter_id": adapter,
            "adapter_version": "1.0.0",
            "model": model,
        },
        "failure": None,
        "raw_output_artifact": None,
    }
    encoded = json.dumps(response, sort_keys=True, separators=(",", ":"))
    if provider == "opencode":
        print(json.dumps({"type": "step_start"}, separators=(",", ":")))
        print(json.dumps({"type": "text", "part": {"text": encoded}}, separators=(",", ":")))
        print(json.dumps({"type": "step_finish"}, separators=(",", ":")))
    else:
        print(json.dumps({"type": "thread.started"}, separators=(",", ":")))
        print(json.dumps({"type": "turn.started"}, separators=(",", ":")))
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": encoded},
                },
                separators=(",", ":"),
            )
        )
        print(json.dumps({"type": "turn.completed"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
