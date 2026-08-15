#!/usr/bin/env python3
"""engineering-controller v0.1.

Deterministic orchestration for a Codex Worker/Reviewer engineering loop.
The core is intentionally project-agnostic and uses only the Python standard
library plus the external Git and Codex CLIs.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


VERSION = "0.1.0"
SCHEMA_VERSION = 1
SKILL_ROOT = Path(__file__).resolve().parents[1]
WORKER_SCHEMA_PATH = SKILL_ROOT / "schemas" / "worker-result.schema.json"
REVIEWER_SCHEMA_PATH = SKILL_ROOT / "schemas" / "reviewer-result.schema.json"
PROJECT_POLICY_SCHEMA_PATH = SKILL_ROOT / "schemas" / "project-policy.schema.json"
WORKER_POLICY_PATH = SKILL_ROOT / "references" / "worker-policy.md"
REVIEWER_POLICY_PATH = SKILL_ROOT / "references" / "reviewer-policy.md"
CONTEXT_BUDGET_PATH = SKILL_ROOT / "references" / "context-budget.md"
HUMAN_POLICY_PATH = SKILL_ROOT / "references" / "human-required-policy.md"
PROJECT_POLICY_FILENAME = ".engineering-controller-policy.json"

DEFAULT_WORKER_MODEL = "gpt-5.6-sol"
DEFAULT_REVIEWER_MODEL = "gpt-5.6-sol"
DEFAULT_WORKER_TIMEOUT = 30 * 60
DEFAULT_REVIEWER_TIMEOUT = 10 * 60
DEFAULT_MAX_SAME_GATE = 3
DEFAULT_MAX_TOTAL_REVIEWS = 6

GLOBAL_HUMAN_FLAGS = frozenset(
    {
        "DESTRUCTIVE_CHANGE",
        "SECURITY_WEAKENING",
        "CREDENTIAL_OR_SECRET",
        "ARCHITECTURE_CHANGE",
        "SCOPE_INCREASE",
        "BUSINESS_BEHAVIOR_CHANGE",
        "COST_INCREASE",
        "PRODUCTION_RISK",
        "OUTSIDE_SPEC",
        "PROTECTED_AREA",
        "GIT_DESTRUCTIVE",
        "BYPASS_SANDBOX",
    }
)

GLOBAL_FORBIDDEN_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sandbox bypass", re.compile(r"--dangerously-bypass-approvals-and-sandbox|--yolo", re.I)),
    ("git force push", re.compile(r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?|-f(?:\s|$))", re.I)),
    ("git push", re.compile(r"\bgit\s+push\b", re.I)),
    ("git commit", re.compile(r"\bgit\s+commit\b", re.I)),
    ("git hard reset", re.compile(r"\bgit\s+reset\s+--hard\b", re.I)),
    ("git destructive clean", re.compile(r"\bgit\s+clean\b[^\r\n]*(?:--force|-f)", re.I)),
    ("git branch deletion", re.compile(r"\bgit\s+branch\s+-(?:d|D)\b", re.I)),
    ("git remote branch deletion", re.compile(r"\bgit\s+push\b[^\r\n]*--delete\b", re.I)),
    ("git merge", re.compile(r"\bgit\s+merge\b", re.I)),
    ("git branch switch", re.compile(r"\bgit\s+switch\b", re.I)),
    ("git checkout", re.compile(r"\bgit\s+checkout\b", re.I)),
    ("git restore", re.compile(r"\bgit\s+restore\b", re.I)),
)

SAFE_ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})
SECRET_FILENAMES = frozenset(
    {
        "credentials.json",
        "secrets.json",
        "secret.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
SECRET_SUFFIXES = frozenset({".pem", ".p12", ".pfx", ".key"})
_LOG_SECRET_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"
)


class ControllerError(RuntimeError):
    category = "FAILED"


class HumanRequired(ControllerError):
    category = "HUMAN_REQUIRED"


class ProtocolError(ControllerError):
    category = "PROTOCOL"


class ProcessError(ControllerError):
    category = "PROCESS"


class ValidationError(ControllerError):
    category = "VALIDATION"


@dataclass(frozen=True)
class ExecResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class Policy:
    protected_paths: list[str] = field(default_factory=list)
    forbidden_commands: list[str] = field(default_factory=list)
    human_required_flags: set[str] = field(default_factory=lambda: set(GLOBAL_HUMAN_FLAGS))
    max_same_gate: int = DEFAULT_MAX_SAME_GATE
    max_total_reviews: int = DEFAULT_MAX_TOTAL_REVIEWS


@dataclass(frozen=True)
class GitSnapshot:
    root: Path
    branch: str
    head: str
    changed_files: tuple[str, ...]
    diff_stat: str
    workspace_fingerprint: str


@dataclass(frozen=True)
class RuntimeConfig:
    codex_command: tuple[str, ...]
    worker_model: str
    reviewer_model: str
    worker_timeout: int
    reviewer_timeout: int
    state_home: Path
    rtk_path: str | None


class RunLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        safe = safe_text(message).replace("\r", " ").replace("\n", " ")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_now()} {safe}\n")


class StateStore:
    def __init__(self, state_home: Path, project_root: Path):
        self.project_root = project_root.resolve()
        state_home = state_home.expanduser().resolve()
        try:
            state_home.relative_to(self.project_root)
        except ValueError:
            pass
        else:
            raise ValidationError("Runtime state directory must be outside the PROJECT TARGET.")
        repo_hash = hashlib.sha256(normalized_path_key(self.project_root).encode("utf-8")).hexdigest()[:20]
        self.repo_dir = state_home / "runs" / repo_hash
        self.current_path = self.repo_dir / "current.json"
        self.lock_path = self.repo_dir / ".lock"
        self._lock_held = False

    def new_run(self) -> tuple[str, Path]:
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        run_dir = self.repo_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        write_json_atomic(self.current_path, {"run_id": run_id})
        return run_id, run_dir

    def get_current_run_dir(self) -> Path:
        if not self.current_path.exists():
            raise ValidationError("No saved engineering-controller run exists for this project.")
        current = load_json_file(self.current_path)
        run_id = current.get("run_id") if isinstance(current, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise ValidationError("Current run pointer is corrupt.")
        run_dir = self.repo_dir / run_id
        if not run_dir.is_dir():
            raise ValidationError("Saved run directory does not exist.")
        return run_dir

    def acquire_lock(self, run_id: str) -> None:
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "run_id": run_id, "started_at": utc_now()})
        try:
            fd = os.open(str(self.lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise HumanRequired(
                f"Another or interrupted engineering-controller run is locked for this project: {self.lock_path}"
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self._lock_held = True

    def release_lock(self) -> None:
        if self._lock_held:
            try:
                self.lock_path.unlink(missing_ok=True)
            finally:
                self._lock_held = False


class CodexRunner:
    def __init__(
        self,
        config: RuntimeConfig,
        project_root: Path,
        run_dir: Path,
        project_forbidden_commands: Sequence[str] = (),
    ):
        self.config = config
        self.project_root = project_root
        self.run_dir = run_dir
        self.project_forbidden_commands = tuple(project_forbidden_commands)

    def worker_initial(self, prompt: str) -> tuple[dict[str, Any], str, list[str]]:
        output_path = self.run_dir / "worker-output.json"
        cmd = self._base_exec("workspace-write", self.config.worker_model, "high") + [
            "--json",
            "--output-schema",
            str(WORKER_SCHEMA_PATH),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        result = run_process(cmd, self.project_root, prompt, self.config.worker_timeout)
        ensure_codex_success(result, "Worker")
        events = parse_jsonl(result.stdout)
        violations = find_forbidden_command_events(events, self.project_forbidden_commands)
        thread_id = extract_thread_id(events)
        if not thread_id:
            raise ProtocolError("Worker JSONL did not contain thread.started/thread_id.")
        payload = load_final_json(output_path, "Worker")
        validate_worker_result(payload)
        return payload, thread_id, violations

    def worker_resume(self, session_id: str, prompt: str) -> tuple[dict[str, Any], list[str]]:
        output_path = self.run_dir / "worker-output.json"
        output_path.unlink(missing_ok=True)
        cmd = self._base_exec("workspace-write", self.config.worker_model, "high") + [
            "--json",
            "--output-schema",
            str(WORKER_SCHEMA_PATH),
            "--output-last-message",
            str(output_path),
            "resume",
            session_id,
            "-",
        ]
        result = run_process(cmd, self.project_root, prompt, self.config.worker_timeout)
        ensure_codex_success(result, "Worker resume")
        events = parse_jsonl(result.stdout)
        violations = find_forbidden_command_events(events, self.project_forbidden_commands)
        resumed_id = extract_thread_id(events)
        if resumed_id and resumed_id != session_id:
            raise ProtocolError("Worker resume returned a different thread_id than the saved Worker session.")
        payload = load_final_json(output_path, "Worker resume")
        validate_worker_result(payload)
        return payload, violations

    def reviewer(self, prompt: str) -> tuple[dict[str, Any], list[str]]:
        output_path = self.run_dir / "reviewer-output.json"
        output_path.unlink(missing_ok=True)
        cmd = self._base_exec("read-only", self.config.reviewer_model, "medium") + [
            "--ephemeral",
            "--json",
            "--output-schema",
            str(REVIEWER_SCHEMA_PATH),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        result = run_process(cmd, self.project_root, prompt, self.config.reviewer_timeout)
        ensure_codex_success(result, "Reviewer")
        events = parse_jsonl(result.stdout)
        violations = find_forbidden_command_events(events, self.project_forbidden_commands)
        payload = load_final_json(output_path, "Reviewer")
        validate_json_schema(payload, load_schema(REVIEWER_SCHEMA_PATH))
        return payload, violations

    def _base_exec(self, sandbox: str, model: str, reasoning: str) -> list[str]:
        return list(self.config.codex_command) + [
            "--ask-for-approval",
            "never",
            "--config",
            f'model_reasoning_effort="{reasoning}"',
            "exec",
            "-C",
            str(self.project_root),
            "--sandbox",
            sandbox,
            "--model",
            model,
            "--color",
            "never",
        ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_text(text: str) -> str:
    return _LOG_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", str(text))


def normalized_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def ec_print(message: str) -> None:
    print(f"[EC] {safe_text(message)}", flush=True)


def truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Unable to read required file: {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValidationError(f"Required file is not valid UTF-8: {path}") from exc


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(load_text(path))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {path}: {exc}") from exc


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_schema(path: Path) -> dict[str, Any]:
    data = load_json_file(path)
    if not isinstance(data, dict) or data.get("type") != "object":
        raise ValidationError(f"Internal schema is invalid or unsupported: {path}")
    return data


def _schema_error(path: str, message: str) -> ValidationError:
    return ValidationError(f"Schema validation failed at {path}: {message}")


def _is_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValidationError(f"Unsupported JSON Schema type in internal schema: {expected}")


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema subset used by the bundled v0.1 schemas."""
    if "oneOf" in schema:
        matches = 0
        last_error: Exception | None = None
        for candidate in schema["oneOf"]:
            try:
                validate_json_schema(value, candidate, path)
                matches += 1
            except ValidationError as exc:
                last_error = exc
        if matches != 1:
            detail = f"oneOf matched {matches} branches"
            if matches == 0 and last_error:
                detail += f"; last error: {last_error}"
            raise _schema_error(path, detail)

    if "const" in schema and value != schema["const"]:
        raise _schema_error(path, f"expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise _schema_error(path, f"value {value!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_is_type(value, item) for item in expected_types):
            raise _schema_error(path, f"expected type {expected_types}, got {type(value).__name__}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise _schema_error(path, "string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise _schema_error(path, "string is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise _schema_error(path, f"string does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise _schema_error(path, "number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise _schema_error(path, "number is above maximum")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise _schema_error(path, "array is shorter than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise _schema_error(path, "array is longer than maxItems")
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for item in value:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker in seen:
                    raise _schema_error(path, "array items must be unique")
                seen.add(marker)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, f"{path}[{index}]")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise _schema_error(path, f"missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise _schema_error(path, f"unexpected properties: {sorted(extras)}")
        for key, child_schema in properties.items():
            if key in value:
                validate_json_schema(value[key], child_schema, f"{path}.{key}")

    for child in schema.get("allOf", []):
        validate_json_schema(value, child, path)

    if "if" in schema and "then" in schema:
        try:
            validate_json_schema(value, schema["if"], path)
        except ValidationError:
            pass
        else:
            validate_json_schema(value, schema["then"], path)


def validate_worker_result(payload: dict[str, Any]) -> None:
    validate_json_schema(payload, load_schema(WORKER_SCHEMA_PATH))
    if payload.get("status") == "COMPLETED":
        failed_checks = [check.get("name", "unnamed") for check in payload.get("checks", []) if check.get("status") == "FAIL"]
        if failed_checks:
            raise ProtocolError(
                "Worker returned COMPLETED with failed checks: " + ", ".join(str(name) for name in failed_checks)
            )


def discover_codex_command() -> tuple[str, ...]:
    override = os.environ.get("ENGINEERING_CONTROLLER_CODEX_CMD")
    if override:
        try:
            parsed = json.loads(override)
        except json.JSONDecodeError as exc:
            raise ValidationError("ENGINEERING_CONTROLLER_CODEX_CMD must be a JSON array of command parts.") from exc
        if not isinstance(parsed, list) or not parsed or not all(isinstance(part, str) and part for part in parsed):
            raise ValidationError("ENGINEERING_CONTROLLER_CODEX_CMD must be a non-empty JSON array of strings.")
        return tuple(parsed)

    executable = shutil.which("codex") or shutil.which("codex.exe")
    if not executable:
        raise ValidationError("Codex CLI was not found in PATH.")
    return (executable,)


def build_runtime_config() -> RuntimeConfig:
    state_home_value = os.environ.get("ENGINEERING_CONTROLLER_STATE_HOME")
    if state_home_value is None:
        state_home_value = str(Path.home() / ".engineering-controller")
    state_home = Path(state_home_value).expanduser()

    def env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValidationError(f"{name} must be an integer.") from exc
        if value < 1:
            raise ValidationError(f"{name} must be >= 1.")
        return value

    return RuntimeConfig(
        codex_command=discover_codex_command(),
        worker_model=os.environ.get("ENGINEERING_CONTROLLER_WORKER_MODEL", DEFAULT_WORKER_MODEL),
        reviewer_model=os.environ.get("ENGINEERING_CONTROLLER_REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL),
        worker_timeout=env_int("ENGINEERING_CONTROLLER_WORKER_TIMEOUT", DEFAULT_WORKER_TIMEOUT),
        reviewer_timeout=env_int("ENGINEERING_CONTROLLER_REVIEWER_TIMEOUT", DEFAULT_REVIEWER_TIMEOUT),
        state_home=state_home,
        rtk_path=shutil.which("rtk") or shutil.which("rtk.exe"),
    )


def terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_process(args: Sequence[str], cwd: Path, input_text: str | None, timeout: int) -> ExecResult:
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            list(args),
            cwd=str(cwd),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise ProcessError(f"Unable to start process {Path(args[0]).name}: {exc}") from exc

    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        return ExecResult(tuple(args), proc.returncode, stdout or "", stderr or "", False)
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc)
        stdout, stderr = proc.communicate()
        return ExecResult(
            tuple(args), proc.returncode if proc.returncode is not None else -1, stdout or "", stderr or "", True
        )


def ensure_codex_success(result: ExecResult, label: str) -> None:
    if result.timed_out:
        raise ProcessError(f"{label} timed out.")
    if result.returncode != 0:
        detail = truncate(result.stderr.strip(), 1200)
        raise ProcessError(
            f"{label} exited with code {result.returncode}{': ' + detail if detail else '.'}"
        )


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"Codex --json emitted invalid JSONL at line {line_no}.") from exc
        if not isinstance(value, dict):
            raise ProtocolError(f"Codex --json emitted a non-object event at line {line_no}.")
        events.append(value)
    if not events:
        raise ProtocolError("Codex --json produced no events.")
    return events


def extract_thread_id(events: Iterable[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def iter_command_events(events: Iterable[dict[str, Any]]) -> Iterable[str]:
    for event in events:
        if not str(event.get("type", "")).startswith("item."):
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            command = item.get("command")
            if isinstance(command, str) and command:
                yield command


def forbidden_command_reason(command: str, project_forbidden: Sequence[str]) -> str | None:
    for label, pattern in GLOBAL_FORBIDDEN_COMMAND_PATTERNS:
        if pattern.search(command):
            return label
    lowered = command.casefold()
    for literal in project_forbidden:
        if literal.casefold() in lowered:
            return f"project-forbidden command pattern: {literal}"
    return None


def find_forbidden_command_events(
    events: Iterable[dict[str, Any]], project_forbidden: Sequence[str]
) -> list[str]:
    violations: list[str] = []
    for command in iter_command_events(events):
        reason = forbidden_command_reason(command, project_forbidden)
        if reason and reason not in violations:
            violations.append(reason)
    return violations


def load_final_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ProtocolError(f"{label} did not produce --output-last-message file.")
    try:
        payload = json.loads(load_text(path))
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{label} final message is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"{label} final message must be a JSON object.")
    return payload


def git_run(root_or_cwd: Path, args: Sequence[str], timeout: int = 30) -> str:
    result = run_process(["git", "-C", str(root_or_cwd), *args], root_or_cwd, None, timeout)
    if result.timed_out:
        raise ProcessError(f"Git command timed out: {' '.join(args)}")
    if result.returncode != 0:
        detail = truncate(result.stderr.strip(), 1000)
        raise ValidationError(f"Git command failed: {' '.join(args)}{': ' + detail if detail else ''}")
    return result.stdout


def discover_git_root(cwd: Path) -> Path:
    if not (shutil.which("git") or shutil.which("git.exe")):
        raise ValidationError("Git CLI was not found in PATH.")
    output = git_run(cwd, ["rev-parse", "--show-toplevel"]).strip()
    if not output:
        raise ValidationError("Current directory is not inside a Git repository.")
    return Path(output).resolve()


def git_branch(root: Path) -> str:
    result = run_process(["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"], root, None, 30)
    if result.timed_out:
        raise ProcessError("Git branch detection timed out.")
    if result.returncode != 0 or not result.stdout.strip():
        raise HumanRequired("Detached HEAD is not allowed for an engineering-controller run.")
    return result.stdout.strip()


def git_head(root: Path) -> str:
    return git_run(root, ["rev-parse", "HEAD"]).strip()


def _nul_paths(output: str) -> set[str]:
    return {item.replace("\\", "/") for item in output.split("\0") if item}


def git_changed_files(root: Path) -> tuple[str, ...]:
    paths: set[str] = set()
    paths.update(_nul_paths(git_run(root, ["diff", "--name-only", "-z"])))
    paths.update(_nul_paths(git_run(root, ["diff", "--cached", "--name-only", "-z"])))
    paths.update(_nul_paths(git_run(root, ["ls-files", "--others", "--exclude-standard", "-z"])))
    return tuple(sorted(paths))


def git_diff_stat(root: Path, changed_files: Sequence[str]) -> str:
    parts: list[str] = []
    unstaged = git_run(root, ["diff", "--stat", "--no-ext-diff"]).strip()
    staged = git_run(root, ["diff", "--cached", "--stat", "--no-ext-diff"]).strip()
    if unstaged:
        parts.append(unstaged)
    if staged:
        parts.append("[staged]\n" + staged)
    tracked = set(_nul_paths(git_run(root, ["diff", "--name-only", "-z"]))) | set(
        _nul_paths(git_run(root, ["diff", "--cached", "--name-only", "-z"]))
    )
    untracked = [path for path in changed_files if path not in tracked]
    if untracked:
        parts.append("[untracked]\n" + "\n".join(untracked[:100]))
    return truncate("\n".join(parts) if parts else "(clean)", 12000)


def workspace_fingerprint(root: Path, changed_files: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(changed_files):
        digest.update(relative.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        path = root / Path(relative)
        try:
            if path.is_symlink():
                digest.update(os.readlink(path).encode("utf-8", errors="replace"))
            elif path.is_file():
                with path.open("rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
            else:
                digest.update(b"<missing-or-nonfile>")
        except OSError as exc:
            digest.update(f"<unreadable:{exc.__class__.__name__}>".encode("ascii", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def git_snapshot(root: Path) -> GitSnapshot:
    changed = git_changed_files(root)
    return GitSnapshot(
        root=root,
        branch=git_branch(root),
        head=git_head(root),
        changed_files=changed,
        diff_stat=git_diff_stat(root, changed),
        workspace_fingerprint=workspace_fingerprint(root, changed),
    )


def is_known_secret_path(relative_path: str) -> bool:
    name = PurePosixPath(relative_path.replace("\\", "/")).name.casefold()
    if name in SAFE_ENV_TEMPLATE_NAMES:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    if name in SECRET_FILENAMES:
        return True
    return any(name.endswith(suffix) for suffix in SECRET_SUFFIXES)


def path_matches_pattern(relative_path: str, pattern: str) -> bool:
    path = relative_path.replace("\\", "/").casefold()
    candidate = pattern.replace("\\", "/").casefold()
    return fnmatch.fnmatch(path, candidate) or fnmatch.fnmatch(PurePosixPath(path).name, candidate)


def protected_path_reason(relative_path: str, policy: Policy) -> str | None:
    normalized = relative_path.replace("\\", "/")
    if PurePosixPath(normalized).name.casefold() == PROJECT_POLICY_FILENAME.casefold():
        return "engineering-controller project policy"
    if is_known_secret_path(normalized):
        return "known secret/credential path"
    for pattern in policy.protected_paths:
        if path_matches_pattern(normalized, pattern):
            return f"project protected path pattern: {pattern}"
    return None


def assert_no_protected_changes(changed_files: Sequence[str], policy: Policy) -> None:
    for relative in changed_files:
        reason = protected_path_reason(relative, policy)
        if reason:
            raise HumanRequired(f"Protected path changed: {relative} ({reason}).")


def ensure_path_inside(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValidationError(f"Path is outside PROJECT TARGET: {path}") from exc
    return path_resolved


def resolve_prompt_path(root: Path, cwd: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if not candidate.is_file():
        raise ValidationError(f"Prompt/SPEC file does not exist: {candidate}")
    return ensure_path_inside(root, candidate)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_project_policy(root: Path) -> Policy:
    schema = load_schema(PROJECT_POLICY_SCHEMA_PATH)
    policy_path = root / PROJECT_POLICY_FILENAME
    data: dict[str, Any] = {}
    if policy_path.exists():
        raw = load_json_file(policy_path)
        if not isinstance(raw, dict):
            raise ValidationError(f"{PROJECT_POLICY_FILENAME} must contain a JSON object.")
        validate_json_schema(raw, schema)
        data = raw

    human_flags = set(GLOBAL_HUMAN_FLAGS)
    human_flags.update(data.get("human_required_flags", []))
    max_same = int(data.get("max_same_gate", DEFAULT_MAX_SAME_GATE))
    max_reviews = int(data.get("max_total_reviews", DEFAULT_MAX_TOTAL_REVIEWS))
    if max_same > DEFAULT_MAX_SAME_GATE or max_reviews > DEFAULT_MAX_TOTAL_REVIEWS:
        raise ValidationError("Project policy may only reduce global loop limits.")

    return Policy(
        protected_paths=list(data.get("protected_paths", [])),
        forbidden_commands=list(data.get("forbidden_commands", [])),
        human_required_flags=human_flags,
        max_same_gate=max_same,
        max_total_reviews=max_reviews,
    )


def policy_as_context(root: Path, policy: Policy) -> str:
    if not (root / PROJECT_POLICY_FILENAME).exists():
        return "No project-specific engineering-controller policy is present."
    return json.dumps(
        {
            "protected_paths": policy.protected_paths,
            "forbidden_commands": policy.forbidden_commands,
            "human_required_flags": sorted(policy.human_required_flags - set(GLOBAL_HUMAN_FLAGS)),
            "max_same_gate": policy.max_same_gate,
            "max_total_reviews": policy.max_total_reviews,
        },
        ensure_ascii=False,
        indent=2,
    )


def gate_fingerprint(gate: dict[str, Any]) -> str:
    return hashlib.sha256(f"{gate.get('type', '')}:{gate.get('key', '')}".encode("utf-8")).hexdigest()


def human_flags_in(values: Sequence[str], policy: Policy) -> list[str]:
    return sorted(set(values) & policy.human_required_flags)


def command_guard_from_gate(gate: dict[str, Any], policy: Policy) -> str | None:
    proposed = gate.get("proposed_action", "")
    return forbidden_command_reason(proposed, policy.forbidden_commands) if isinstance(proposed, str) else None


def check_snapshot_invariants(snapshot: GitSnapshot, state: dict[str, Any], policy: Policy) -> None:
    if snapshot.branch != state["branch"]:
        raise HumanRequired(f"Git branch changed from {state['branch']} to {snapshot.branch}.")
    if snapshot.head != state["initial_head"]:
        raise HumanRequired("Git HEAD changed during the run; v0.1 does not allow automatic commits or history changes.")
    assert_no_protected_changes(snapshot.changed_files, policy)


def build_worker_initial_prompt(root: Path, prompt_path: Path, policy: Policy) -> str:
    original = load_text(prompt_path)
    relative_prompt = prompt_path.relative_to(root).as_posix()
    return f"""ENGINEERING-CONTROLLER WORKER RUN

PROJECT TARGET: {root}
GOVERNING PROMPT/SPEC PATH: {relative_prompt}

=== CONTROLLER WORKER POLICY ===
{load_text(WORKER_POLICY_PATH)}

=== CONTEXT BUDGET ===
{load_text(CONTEXT_BUDGET_PATH)}

=== PROJECT RESTRICTIONS ===
{policy_as_context(root, policy)}

=== GOVERNING PROJECT PROMPT/SPEC ===
{original}
=== END GOVERNING PROJECT PROMPT/SPEC ===

Implement and validate the requested task inside the authorized PROJECT TARGET. Continue autonomously while inside scope and policy. Finish with only the JSON object required by the Worker schema.
"""


def build_reviewer_prompt(
    root: Path,
    prompt_path: Path,
    gate: dict[str, Any],
    snapshot: GitSnapshot,
    policy: Policy,
) -> str:
    changed = "\n".join(snapshot.changed_files[:200]) if snapshot.changed_files else "(none)"
    return f"""ENGINEERING-CONTROLLER INDEPENDENT REVIEW

=== REVIEWER POLICY ===
{load_text(REVIEWER_POLICY_PATH)}

=== HUMAN_REQUIRED POLICY ===
{load_text(HUMAN_POLICY_PATH)}

=== CONTEXT BUDGET ===
{load_text(CONTEXT_BUDGET_PATH)}

PROJECT TARGET: {root}
GOVERNING PROJECT PROMPT/SPEC PATH: {prompt_path.relative_to(root).as_posix()}
BRANCH: {snapshot.branch}
HEAD: {snapshot.head}

CHANGED FILES:
{changed}

GIT DIFF STAT:
{snapshot.diff_stat}

PROJECT RESTRICTIONS:
{policy_as_context(root, policy)}

WORKER GATE JSON:
{json.dumps(gate, ensure_ascii=False, indent=2)}

Review only this gate. Read the governing prompt/SPEC or evidence-referenced files only as needed. Do not modify anything. Finish with only the JSON object required by the Reviewer schema.
"""


def build_worker_review_followup(decision: dict[str, Any], gate: dict[str, Any], snapshot: GitSnapshot) -> str:
    return f"""ENGINEERING-CONTROLLER GATE RESPONSE

Gate type: {gate['type']}
Gate key: {gate['key']}
Reviewer decision: {decision['decision']}
Reviewer reason: {decision['reason']}
Reviewer instructions: {decision['instructions']}

Current Git diff stat:
{snapshot.diff_stat}

Continue the same Worker task. Apply only the approved/revised direction. Global safety policy remains unchanged. APPROVE never authorizes a globally prohibited action. Finish again with only the Worker-schema JSON object.
"""


def build_human_resume_followup(state: dict[str, Any], snapshot: GitSnapshot, note: str | None) -> str:
    gate = state.get("current_gate") or {}
    note_text = note.strip() if note else "(no explicit human note supplied; re-evaluate whether repository state resolves the gate)"
    return f"""ENGINEERING-CONTROLLER HUMAN RESUME

The previous automated run stopped at HUMAN_REQUIRED.
Previous gate type: {gate.get('type', '(unknown)')}
Previous gate key: {gate.get('key', '(unknown)')}
Human note: {note_text}

Current Git diff stat:
{snapshot.diff_stat}

Re-evaluate the PROJECT TARGET and continue only if the human action/note genuinely resolves the gate within the governing prompt/SPEC and safety policy. A resume is never authorization for globally prohibited operations. If a material decision remains unresolved, return GATE_REQUIRED again. Finish with only the Worker-schema JSON object.
"""


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json_atomic(run_dir / "state.json", state)


def load_state(run_dir: Path) -> dict[str, Any]:
    raw = load_json_file(run_dir / "state.json")
    if not isinstance(raw, dict):
        raise ValidationError("Saved state is corrupt: expected JSON object.")
    required = {
        "schema_version",
        "run_id",
        "status",
        "project_root",
        "prompt_path",
        "prompt_hash",
        "branch",
        "initial_head",
        "worker_runs",
        "review_count",
        "gate_counts",
        "started_at",
    }
    missing = required - set(raw)
    if missing:
        raise ValidationError(f"Saved state is corrupt; missing fields: {sorted(missing)}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("Saved state schema version is unsupported.")
    if not isinstance(raw.get("gate_counts"), dict):
        raise ValidationError("Saved state gate_counts is corrupt.")
    return raw


def record_human_required(
    state: dict[str, Any], run_dir: Path, logger: RunLogger, reason: str, snapshot: GitSnapshot | None
) -> None:
    state["status"] = "HUMAN_REQUIRED"
    state["human_required_reason"] = safe_text(reason)
    if snapshot is not None:
        state["workspace_fingerprint_at_human"] = snapshot.workspace_fingerprint
    save_state(run_dir, state)
    logger.write(f"HUMAN_REQUIRED reason={reason}")
    ec_print("HUMAN_REQUIRED")
    print(f"Motivo: {safe_text(reason)}", flush=True)
    print(f"Estado salvo: {run_dir / 'state.json'}", flush=True)


def record_failed(state: dict[str, Any], run_dir: Path, logger: RunLogger, reason: str) -> None:
    state["status"] = "FAILED"
    state["failure_reason"] = safe_text(reason)
    save_state(run_dir, state)
    logger.write(f"FAILED reason={reason}")
    ec_print(f"FAILED — {reason}")


def final_validation(root: Path, state: dict[str, Any], policy: Policy) -> GitSnapshot:
    snapshot = git_snapshot(root)
    check_snapshot_invariants(snapshot, state, policy)
    return snapshot


def process_worker_result(
    *,
    worker_result: dict[str, Any],
    command_violations: Sequence[str],
    state: dict[str, Any],
    run_dir: Path,
    logger: RunLogger,
    root: Path,
    prompt_path: Path,
    policy: Policy,
    runner: CodexRunner,
) -> int:
    while True:
        state["worker_runs"] += 1
        write_json_atomic(run_dir / "worker-result.json", worker_result)
        state["last_worker_status"] = worker_result["status"]
        save_state(run_dir, state)
        logger.write(f"Worker #{state['worker_runs']} status={worker_result['status']}")

        snapshot = git_snapshot(root)
        check_snapshot_invariants(snapshot, state, policy)

        if command_violations:
            record_human_required(
                state,
                run_dir,
                logger,
                "Worker executed a prohibited command category: " + ", ".join(command_violations),
                snapshot,
            )
            return 2

        status = worker_result["status"]
        if status == "FAILED":
            failure = worker_result.get("failure") or {}
            reason = f"Worker failed ({failure.get('category', 'TASK')}): {failure.get('reason', worker_result['summary'])}"
            record_failed(state, run_dir, logger, reason)
            return 1

        if status == "COMPLETED":
            state["status"] = "FINAL_VALIDATION"
            save_state(run_dir, state)
            final_snapshot = final_validation(root, state, policy)
            state["status"] = "COMPLETED"
            state["final_changed_files"] = list(final_snapshot.changed_files)
            state["completed_at"] = utc_now()
            save_state(run_dir, state)
            logger.write("Final validation OK; COMPLETED")
            ec_print("Final validation OK")
            ec_print("COMPLETED")
            return 0

        gate = worker_result.get("gate")
        if not isinstance(gate, dict):
            raise ProtocolError("GATE_REQUIRED Worker result has no valid gate object.")

        state["status"] = "GATE_PENDING"
        state["current_gate"] = gate
        fingerprint = gate_fingerprint(gate)
        gate_counts = state["gate_counts"]
        gate_counts[fingerprint] = int(gate_counts.get(fingerprint, 0)) + 1
        save_state(run_dir, state)
        logger.write(f"Gate type={gate['type']} key={gate['key']} count={gate_counts[fingerprint]}")
        ec_print(f"Gate: {gate['type']} / {gate['key']}")

        if gate_counts[fingerprint] > policy.max_same_gate:
            record_human_required(
                state,
                run_dir,
                logger,
                f"Same gate exceeded automatic limit ({policy.max_same_gate}): {gate['type']} / {gate['key']}",
                snapshot,
            )
            return 2

        command_reason = command_guard_from_gate(gate, policy)
        if command_reason:
            record_human_required(
                state,
                run_dir,
                logger,
                f"Gate proposes prohibited operation: {command_reason}",
                snapshot,
            )
            return 2

        worker_human_flags = human_flags_in(gate.get("risk_flags", []), policy)
        if worker_human_flags:
            record_human_required(
                state,
                run_dir,
                logger,
                "Worker classified human-required risk: " + ", ".join(worker_human_flags),
                snapshot,
            )
            return 2

        if state["review_count"] >= policy.max_total_reviews:
            record_human_required(
                state,
                run_dir,
                logger,
                f"Total Reviewer limit reached ({policy.max_total_reviews}).",
                snapshot,
            )
            return 2

        state["status"] = "REVIEW"
        save_state(run_dir, state)
        review_number = state["review_count"] + 1
        ec_print(f"Reviewer #{review_number} iniciado")
        reviewer_result, reviewer_command_violations = runner.reviewer(
            build_reviewer_prompt(root, prompt_path, gate, snapshot, policy)
        )
        state["review_count"] = review_number
        state["last_review"] = reviewer_result
        write_json_atomic(run_dir / "reviewer-result.json", reviewer_result)
        save_state(run_dir, state)
        logger.write(f"Reviewer #{review_number} decision={reviewer_result['decision']}")
        ec_print(f"Reviewer #{review_number}: {reviewer_result['decision']}")

        snapshot = git_snapshot(root)
        check_snapshot_invariants(snapshot, state, policy)

        if reviewer_command_violations:
            record_human_required(
                state,
                run_dir,
                logger,
                "Reviewer executed a prohibited command category: " + ", ".join(reviewer_command_violations),
                snapshot,
            )
            return 2

        review_human_flags = human_flags_in(reviewer_result.get("risk_flags", []), policy)
        combined_flags = sorted(set(worker_human_flags) | set(review_human_flags))
        if combined_flags:
            record_human_required(
                state,
                run_dir,
                logger,
                "Reviewer/Worker classified human-required risk: " + ", ".join(combined_flags),
                snapshot,
            )
            return 2

        if reviewer_result["decision"] == "HUMAN_REQUIRED":
            record_human_required(state, run_dir, logger, reviewer_result["reason"], snapshot)
            return 2
        if reviewer_result["decision"] not in {"APPROVE", "REVISE"}:
            raise ProtocolError(f"Unsupported Reviewer decision: {reviewer_result['decision']}")

        session_id = state.get("worker_session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ProtocolError("Worker session ID is missing; cannot resume after review.")

        state["status"] = "WORKER"
        save_state(run_dir, state)
        ec_print(f"Worker #{state['worker_runs'] + 1} retomado")
        worker_result, command_violations = runner.worker_resume(
            session_id, build_worker_review_followup(reviewer_result, gate, snapshot)
        )


def execute_command(prompt_arg: str, cwd: Path) -> int:
    load_schema(WORKER_SCHEMA_PATH)
    load_schema(REVIEWER_SCHEMA_PATH)
    load_schema(PROJECT_POLICY_SCHEMA_PATH)

    config = build_runtime_config()
    root = discover_git_root(cwd)
    prompt_path = resolve_prompt_path(root, cwd, prompt_arg)
    policy = load_project_policy(root)

    prompt_relative = prompt_path.relative_to(root).as_posix()
    prompt_guard = protected_path_reason(prompt_relative, policy)
    if prompt_guard:
        raise HumanRequired(f"Prompt/SPEC path is protected: {prompt_relative} ({prompt_guard}).")

    snapshot = git_snapshot(root)
    if snapshot.changed_files:
        raise HumanRequired(
            "Working tree is not clean before Worker start: " + ", ".join(snapshot.changed_files[:20])
        )

    store = StateStore(config.state_home, root)
    run_id, run_dir = store.new_run()
    logger = RunLogger(run_dir / "run.log")
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "controller_version": VERSION,
        "run_id": run_id,
        "status": "PREFLIGHT",
        "project_root": str(root),
        "prompt_path": str(prompt_path),
        "prompt_hash": file_sha256(prompt_path),
        "branch": snapshot.branch,
        "initial_head": snapshot.head,
        "worker_session_id": None,
        "worker_runs": 0,
        "review_count": 0,
        "gate_counts": {},
        "current_gate": None,
        "last_review": None,
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "warnings": [],
    }

    try:
        store.acquire_lock(run_id)
        if not config.rtk_path:
            state["warnings"].append("RTK_NOT_FOUND")
            ec_print("WARN: RTK não encontrado; execução continuará sem RTK")
        logger.write(f"START project={root} branch={snapshot.branch} head={snapshot.head} prompt={prompt_relative}")
        save_state(run_dir, state)
        ec_print("Preflight OK")
        ec_print("Worker #1 iniciado")
        state["status"] = "WORKER"
        save_state(run_dir, state)

        runner = CodexRunner(config, root, run_dir, policy.forbidden_commands)
        worker_result, thread_id, command_violations = runner.worker_initial(
            build_worker_initial_prompt(root, prompt_path, policy)
        )
        state["worker_session_id"] = thread_id
        save_state(run_dir, state)
        return process_worker_result(
            worker_result=worker_result,
            command_violations=command_violations,
            state=state,
            run_dir=run_dir,
            logger=logger,
            root=root,
            prompt_path=prompt_path,
            policy=policy,
            runner=runner,
        )
    except HumanRequired as exc:
        try:
            current = git_snapshot(root)
        except ControllerError:
            current = None
        record_human_required(state, run_dir, logger, str(exc), current)
        return 2
    except ControllerError as exc:
        record_failed(state, run_dir, logger, f"{exc.category}: {exc}")
        return 1
    except KeyboardInterrupt:
        record_failed(state, run_dir, logger, "Execution interrupted by user.")
        return 130
    finally:
        store.release_lock()


def resume_command(note: str | None, cwd: Path) -> int:
    load_schema(WORKER_SCHEMA_PATH)
    load_schema(REVIEWER_SCHEMA_PATH)
    load_schema(PROJECT_POLICY_SCHEMA_PATH)

    config = build_runtime_config()
    root = discover_git_root(cwd)
    store = StateStore(config.state_home, root)
    run_dir = store.get_current_run_dir()
    state = load_state(run_dir)
    logger = RunLogger(run_dir / "run.log")

    if state.get("status") != "HUMAN_REQUIRED":
        raise ValidationError(f"Current run is {state.get('status')}, not HUMAN_REQUIRED; nothing to resume.")
    if normalized_path_key(Path(state["project_root"])) != normalized_path_key(root):
        raise ValidationError("Saved state belongs to a different PROJECT TARGET.")

    prompt_path = Path(state["prompt_path"])
    if not prompt_path.is_file():
        raise ValidationError("Saved governing prompt/SPEC no longer exists.")
    ensure_path_inside(root, prompt_path)
    if file_sha256(prompt_path) != state["prompt_hash"]:
        raise HumanRequired("Governing prompt/SPEC changed after the run started; start a new execute run.")

    policy = load_project_policy(root)
    snapshot = git_snapshot(root)
    check_snapshot_invariants(snapshot, state, policy)
    session_id = state.get("worker_session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValidationError(
            "Saved HUMAN_REQUIRED state has no Worker session. Resolve the preflight issue and run execute again."
        )

    try:
        store.acquire_lock(state["run_id"])
        logger.write("RESUME requested")
        ec_print(f"Resume: {state['run_id']}")
        ec_print(f"Worker #{state['worker_runs'] + 1} retomado")
        runner = CodexRunner(config, root, run_dir, policy.forbidden_commands)
        state["status"] = "WORKER"
        save_state(run_dir, state)
        worker_result, command_violations = runner.worker_resume(
            session_id, build_human_resume_followup(state, snapshot, note)
        )
        return process_worker_result(
            worker_result=worker_result,
            command_violations=command_violations,
            state=state,
            run_dir=run_dir,
            logger=logger,
            root=root,
            prompt_path=prompt_path,
            policy=policy,
            runner=runner,
        )
    except HumanRequired as exc:
        try:
            current = git_snapshot(root)
        except ControllerError:
            current = None
        record_human_required(state, run_dir, logger, str(exc), current)
        return 2
    except ControllerError as exc:
        record_failed(state, run_dir, logger, f"{exc.category}: {exc}")
        return 1
    except KeyboardInterrupt:
        record_failed(state, run_dir, logger, "Execution interrupted by user.")
        return 130
    finally:
        store.release_lock()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engineering-controller",
        description="Deterministic Worker/Reviewer Engineering Loop for Codex CLI.",
    )
    parser.add_argument("--version", action="version", version=f"engineering-controller {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    execute_parser = subparsers.add_parser("execute", help="Start a new Engineering Loop.")
    execute_parser.add_argument("prompt", help="Prompt/SPEC file inside the PROJECT TARGET.")

    resume_parser = subparsers.add_parser("resume", help="Resume the current HUMAN_REQUIRED run.")
    resume_parser.add_argument("note", nargs="*", help="Optional human resolution note for the resumed Worker.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd = Path.cwd().resolve()
    try:
        if args.command == "execute":
            return execute_command(args.prompt, cwd)
        if args.command == "resume":
            return resume_command(" ".join(args.note).strip() or None, cwd)
        raise ValidationError("Unsupported command.")
    except HumanRequired as exc:
        ec_print("HUMAN_REQUIRED")
        print(f"Motivo: {safe_text(str(exc))}", flush=True)
        return 2
    except ControllerError as exc:
        ec_print(f"FAILED — {exc.category}: {exc}")
        return 1
    except KeyboardInterrupt:
        ec_print("FAILED — interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
