"""
logger.py — writes the runtime operational audit trail to gemini.md.

This is NOT hidden chain-of-thought: it records which tool was called, with
which (sanitized) arguments, what the observation was, and the final
validated decision. No secrets, no raw model dumps, no full image bytes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import config


class AgentLogger:
    def __init__(self, path: Path = config.AGENTS_LOG_MD) -> None:
        self.path = path
        self._buffer: list[str] = []
        self._buffer.append("# Agent Run\n")
        self._buffer.append(f"\nRun started: {datetime.now(timezone.utc).isoformat()}\n")

    def start_request(self, request_id: str, request_text: str) -> None:
        self._buffer.append(f"\n## Request: {request_id}\n")
        self._buffer.append("\n### User request\n\n" + (request_text or "(no free-text request)") + "\n")

    def step(self, step_no: int, action: str, arguments: dict, observation: str) -> None:
        args_lines = "\n".join(f"- {k}: {v}" for k, v in arguments.items()) or "- (none)"
        self._buffer.append(
            f"\n### Agent Step {step_no}\n\nAction: `{action}`\n\nArguments:\n{args_lines}\n\n"
            f"### Observation\n\n{observation}\n"
        )

    def validation(self, status: str, errors: list[str] | None = None) -> None:
        self._buffer.append(f"\n### Validation\n\nStatus: {status}\n")
        if errors:
            for e in errors:
                self._buffer.append(f"- {e}\n")

    def final_decision(self, method: str, status: str, explanation: str) -> None:
        self._buffer.append(
            f"\n### Final decision\n\nMethod: {method}\nStatus: {status}\nExplanation: {explanation}\n"
        )

    def failure(self, request_id: str, error: str) -> None:
        self._buffer.append(f"\n### Failure processing {request_id}\n\n{error}\n")

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(self._buffer), encoding="utf-8")
