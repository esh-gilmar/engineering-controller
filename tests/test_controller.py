from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CONTROLLER = SCRIPTS_DIR / "controller.py"
FAKE_CODEX = REPO_ROOT / "tests" / "fakes" / "fake_codex.py"
sys.path.insert(0, str(SCRIPTS_DIR))

import controller  # noqa: E402


class SyntheticRepo:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state_home = self.base / "state"
        self.fake_state = self.base / "fake-counts.json"
        self._git("init")
        self._git("checkout", "-b", "main")
        self._git("config", "user.name", "Engineering Controller Tests")
        self._git("config", "user.email", "engineering-controller@example.invalid")
        (self.root / "TASK.md").write_text(
            "# Synthetic task\n\nCreate the smallest safe local result and validate it.\n",
            encoding="utf-8",
        )
        self._git("add", "TASK.md")
        self._git("commit", "-m", "test: baseline")

    def close(self):
        self.temp.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def commit_policy(self, policy: dict) -> None:
        (self.root / controller.PROJECT_POLICY_FILENAME).write_text(json.dumps(policy), encoding="utf-8")
        self._git("add", controller.PROJECT_POLICY_FILENAME)
        self._git("commit", "-m", "test: restrictive controller policy")

    def env(self, scenario: str, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "ENGINEERING_CONTROLLER_CODEX_CMD": json.dumps([sys.executable, str(FAKE_CODEX)]),
                "ENGINEERING_CONTROLLER_STATE_HOME": str(self.state_home),
                "ENGINEERING_CONTROLLER_WORKER_TIMEOUT": "5",
                "ENGINEERING_CONTROLLER_REVIEWER_TIMEOUT": "5",
                "EC_FAKE_SCENARIO": scenario,
                "EC_FAKE_STATE": str(self.fake_state),
            }
        )
        env.update(extra)
        return env

    def run(self, scenario: str, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = self.env(scenario)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(CONTROLLER), *args],
            cwd=str(self.root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def counts(self) -> dict[str, int]:
        if not self.fake_state.exists():
            return {"worker": 0, "reviewer": 0, "resume": 0}
        return json.loads(self.fake_state.read_text(encoding="utf-8"))

    def latest_state_file(self) -> Path:
        files = list((self.state_home / "runs").glob("*/*/state.json"))
        if not files:
            raise AssertionError("No state file found")
        return max(files, key=lambda p: p.stat().st_mtime_ns)

    def latest_state(self) -> dict:
        return json.loads(self.latest_state_file().read_text(encoding="utf-8"))


class ControllerFlowTests(unittest.TestCase):
    def setUp(self):
        self.repo = SyntheticRepo()

    def tearDown(self):
        self.repo.close()

    def test_a_completed_skips_reviewer(self):
        result = self.repo.run("completed", "execute", "TASK.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[EC] COMPLETED", result.stdout)
        self.assertTrue((self.repo.root / "result.txt").exists())
        self.assertEqual(self.repo.counts()["reviewer"], 0)
        self.assertEqual(self.repo.latest_state()["status"], "COMPLETED")

    def test_b_gate_revise_then_completed(self):
        result = self.repo.run("revise", "execute", "TASK.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Reviewer #1: REVISE", result.stdout)
        counts = self.repo.counts()
        self.assertEqual(counts["reviewer"], 1)
        self.assertEqual(counts["resume"], 1)
        self.assertEqual(self.repo.latest_state()["worker_runs"], 2)

    def test_c_gate_approve_then_completed(self):
        result = self.repo.run("approve", "execute", "TASK.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Reviewer #1: APPROVE", result.stdout)
        counts = self.repo.counts()
        self.assertEqual(counts["reviewer"], 1)
        self.assertEqual(counts["resume"], 1)

    def test_d_hard_guard_blocks_force_push_before_reviewer(self):
        result = self.repo.run("human_command", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("HUMAN_REQUIRED", result.stdout)
        self.assertEqual(self.repo.counts()["reviewer"], 0)
        self.assertEqual(self.repo.latest_state()["status"], "HUMAN_REQUIRED")
        self.assertIn("resume disponível", result.stdout)

    def test_resume_after_human_required_reuses_worker_session(self):
        first = self.repo.run("human_command", "execute", "TASK.md")
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        second = self.repo.run(
            "completed",
            "resume",
            "Use",
            "a",
            "safe",
            "local",
            "alternative",
            "only",
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("[EC] COMPLETED", second.stdout)
        self.assertGreaterEqual(self.repo.counts()["resume"], 1)

    def test_worker_invalid_json_fails_closed(self):
        result = self.repo.run("invalid_worker_json", "execute", "TASK.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAILED", result.stdout)

    def test_reviewer_invalid_json_fails_closed(self):
        result = self.repo.run("invalid_reviewer_json", "execute", "TASK.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAILED", result.stdout)

    def test_worker_nonzero_exit_fails_closed(self):
        result = self.repo.run("worker_exit", "execute", "TASK.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Worker exited with code 7", result.stdout)

    def test_reviewer_nonzero_exit_fails_closed(self):
        result = self.repo.run("reviewer_exit", "execute", "TASK.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Reviewer exited with code 9", result.stdout)

    def test_worker_timeout_fails_closed(self):
        result = self.repo.run(
            "timeout",
            "execute",
            "TASK.md",
            env_extra={"ENGINEERING_CONTROLLER_WORKER_TIMEOUT": "1", "EC_FAKE_SLEEP": "3"},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("timed out", result.stdout)
        self.assertEqual(self.repo.latest_state()["status"], "FAILED")
        self.assertIsNone(self.repo.latest_state().get("active_worker_pid"))

    def test_same_gate_loop_limit_stops_for_human(self):
        result = self.repo.run("gate_loop", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Same gate exceeded automatic limit", result.stdout)
        self.assertEqual(self.repo.counts()["reviewer"], 3)

    def test_project_policy_can_reduce_loop_limit(self):
        self.repo.commit_policy({"max_same_gate": 1})
        result = self.repo.run("gate_loop", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.repo.counts()["reviewer"], 1)

    def test_known_secret_path_change_requires_human(self):
        result = self.repo.run("secret_change", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Protected path changed: .env", result.stdout)

    def test_controller_project_policy_is_protected_during_run(self):
        self.repo.commit_policy({"max_same_gate": 2})
        result = self.repo.run("policy_change", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Protected path changed: .engineering-controller-policy.json", result.stdout)

    def test_prohibited_command_event_requires_human(self):
        result = self.repo.run("command_violation", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("prohibited command category", result.stdout)

    def test_project_forbidden_command_event_requires_human(self):
        self.repo.commit_policy({"forbidden_commands": ["synthetic-custom-forbidden"]})
        result = self.repo.run("project_command_violation", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("project-forbidden command pattern", result.stdout)

    def test_completed_with_failed_check_fails_closed(self):
        result = self.repo.run("completed_failed_check", "execute", "TASK.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("COMPLETED with failed checks", result.stdout)

    def test_dirty_worktree_requires_human_before_worker(self):
        source_dir = self.repo.root / "src"
        source_dir.mkdir()
        source = source_dir / "example.py"
        source.write_text("value = 1\n", encoding="utf-8")
        self.repo._git("add", "src/example.py")
        self.repo._git("commit", "-m", "test: add source fixture")
        source.write_text("value = 2\n", encoding="utf-8")
        result = self.repo.run("completed", "execute", "TASK.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Working tree has changes other than", result.stdout)
        self.assertIn("src/example.py", result.stdout)
        self.assertIn("Nenhum run resumível foi criado", result.stdout)
        self.assertEqual(self.repo.counts()["worker"], 0)
        self.assertFalse((self.repo.state_home / "runs").exists())

    def test_untracked_prompt_is_allowed_as_only_preexisting_change(self):
        docs = self.repo.root / "docs"
        docs.mkdir()
        spec = docs / "test-spec.md"
        spec.write_text("# input spec\n", encoding="utf-8")
        result = self.repo.run("completed", "execute", "docs/test-spec.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.repo.counts()["worker"], 1)
        state = self.repo.latest_state()
        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(state["prompt_path"], str(spec.resolve()))
        self.assertNotIn("docs/test-spec.md", state["final_changed_files"])
        self.assertEqual(state["final_changed_files"], ["result.txt"])

    def test_modified_prompt_is_allowed_as_only_preexisting_change(self):
        (self.repo.root / "TASK.md").write_text("# changed input spec\n", encoding="utf-8")
        result = self.repo.run("completed", "execute", "TASK.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.repo.counts()["worker"], 1)
        self.assertNotIn("TASK.md", self.repo.latest_state()["final_changed_files"])

    def test_prompt_plus_other_dirty_file_still_blocks(self):
        docs = self.repo.root / "docs"
        docs.mkdir()
        spec = docs / "test-spec.md"
        spec.write_text("# input spec\n", encoding="utf-8")
        source_dir = self.repo.root / "src"
        source_dir.mkdir()
        source = source_dir / "example.py"
        source.write_text("value = 1\n", encoding="utf-8")
        self.repo._git("add", "src/example.py")
        self.repo._git("commit", "-m", "test: add source fixture")
        source.write_text("value = 2\n", encoding="utf-8")
        result = self.repo.run("completed", "execute", "docs/test-spec.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("src/example.py", result.stdout)
        self.assertNotIn("docs/test-spec.md,", result.stdout)
        self.assertEqual(self.repo.counts()["worker"], 0)

    def test_prompt_outside_project_target_fails_validation(self):
        outside = self.repo.base / "outside-spec.md"
        outside.write_text("# outside\n", encoding="utf-8")
        result = self.repo.run("completed", "execute", str(outside))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Path is outside PROJECT TARGET", result.stdout)
        self.assertEqual(self.repo.counts()["worker"], 0)

    def test_missing_prompt_fails(self):
        result = self.repo.run("completed", "execute", "MISSING.md")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("does not exist", result.stdout)

    def test_resume_without_state_fails(self):
        result = self.repo.run("completed", "resume")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("No saved engineering-controller run", result.stdout)

    def test_stale_worker_with_saved_session_resumes_same_run(self):
        first = self.repo.run("human_command", "execute", "TASK.md")
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        state_file = self.repo.latest_state_file()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        run_id = state["run_id"]
        session_id = state["worker_session_id"]
        state["status"] = "WORKER"
        state["active_worker_pid"] = None
        state_file.write_text(json.dumps(state), encoding="utf-8")

        resumed = self.repo.run("completed", "resume")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertIn("Recovered stale WORKER", resumed.stdout)
        final_state = self.repo.latest_state()
        self.assertEqual(final_state["run_id"], run_id)
        self.assertEqual(final_state["worker_session_id"], session_id)
        self.assertEqual(final_state["status"], "COMPLETED")
        self.assertGreaterEqual(self.repo.counts()["resume"], 1)

    def test_active_worker_state_does_not_start_second_worker(self):
        first = self.repo.run("human_command", "execute", "TASK.md")
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        state_file = self.repo.latest_state_file()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        before_resume_count = self.repo.counts()["resume"]
        state["status"] = "WORKER"
        state["active_worker_pid"] = os.getpid()
        state_file.write_text(json.dumps(state), encoding="utf-8")

        resumed = self.repo.run("completed", "resume")
        self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
        self.assertIn("still active", resumed.stdout)
        self.assertIn("Do not start another Worker", resumed.stdout)
        self.assertEqual(self.repo.counts()["resume"], before_resume_count)
        self.assertEqual(self.repo.latest_state()["status"], "WORKER")

    def test_stale_worker_without_session_fails_controlled_and_preserves_delivery(self):
        first = self.repo.run("human_command", "execute", "TASK.md")
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        delivery = self.repo.root / "partial-delivery.txt"
        delivery.write_text("preserve me\n", encoding="utf-8")
        state_file = self.repo.latest_state_file()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["status"] = "WORKER"
        state["worker_session_id"] = None
        state["active_worker_pid"] = None
        state_file.write_text(json.dumps(state), encoding="utf-8")

        resumed = self.repo.run("completed", "resume")
        self.assertEqual(resumed.returncode, 1, resumed.stdout + resumed.stderr)
        self.assertIn("no Worker session ID was persisted", resumed.stdout)
        self.assertEqual(delivery.read_text(encoding="utf-8"), "preserve me\n")
        self.assertEqual(self.repo.latest_state()["status"], "FAILED")
        self.assertFalse(self.repo.latest_state().get("resume_available", True))

    def test_corrupt_state_fails_closed(self):
        first = self.repo.run("human_command", "execute", "TASK.md")
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        self.repo.latest_state_file().write_text('{"oops": true}\n', encoding="utf-8")
        result = self.repo.run("completed", "resume")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Saved state is corrupt", result.stdout)


class ControllerUnitTests(unittest.TestCase):
    def test_transport_schemas_use_structured_outputs_keywords_only(self):
        unsupported = {
            "oneOf",
            "allOf",
            "if",
            "then",
            "minLength",
            "maxLength",
            "pattern",
            "minItems",
            "maxItems",
            "uniqueItems",
            "minimum",
            "maximum",
        }

        def find_unsupported(value: object, path: str = "$.") -> list[str]:
            found: list[str] = []
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in unsupported:
                        found.append(f"{path}{key}")
                    found.extend(find_unsupported(child, f"{path}{key}."))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    found.extend(find_unsupported(child, f"{path}[{index}]."))
            return found

        worker_schema = controller.load_schema(controller.WORKER_SCHEMA_PATH)
        reviewer_schema = controller.load_schema(controller.REVIEWER_SCHEMA_PATH)
        self.assertIn("anyOf", json.dumps(worker_schema))
        self.assertEqual([], find_unsupported(worker_schema))
        self.assertEqual([], find_unsupported(reviewer_schema))

    def test_worker_status_invariants_are_enforced_after_structural_validation(self):
        gate = {
            "type": "CUSTOM",
            "key": "custom-key",
            "reason": "review",
            "proposed_action": "safe action",
            "risk_flags": [],
            "evidence": [],
        }
        invalid_payloads = [
            {"status": "COMPLETED", "gate": gate, "failure": None},
            {"status": "GATE_REQUIRED", "gate": None, "failure": None},
            {"status": "FAILED", "gate": None, "failure": None},
        ]
        for partial in invalid_payloads:
            with self.subTest(status=partial["status"]):
                payload = {
                    "status": partial["status"],
                    "summary": "synthetic",
                    "checks": [],
                    "gate": partial["gate"],
                    "failure": partial["failure"],
                }
                with self.assertRaises(controller.ProtocolError):
                    controller.validate_worker_result(payload)

    def test_semantic_validation_rejects_duplicate_risk_flags(self):
        payload = {
            "status": "GATE_REQUIRED",
            "summary": "gate",
            "checks": [],
            "gate": {
                "type": "CUSTOM",
                "key": "custom-key",
                "reason": "review",
                "proposed_action": "safe action",
                "risk_flags": ["CUSTOM_RISK", "CUSTOM_RISK"],
                "evidence": [],
            },
            "failure": None,
        }
        with self.assertRaises(controller.ProtocolError):
            controller.validate_worker_result(payload)

        reviewer_payload = {
            "decision": "APPROVE",
            "reason": "safe",
            "instructions": "",
            "risk_flags": ["CUSTOM_RISK", "CUSTOM_RISK"],
        }
        with self.assertRaises(controller.ProtocolError):
            controller.validate_reviewer_result(reviewer_payload)

    def test_stderr_tail_preserves_terminal_error(self):
        stderr = ("WARN plugin initialization\n" * 100) + "ERROR terminal Codex startup failure"
        with self.assertRaises(controller.ProcessError) as raised:
            controller.ensure_codex_success(
                controller.ExecResult(("codex",), 1, "", stderr, False),
                "Worker",
            )
        self.assertIn("ERROR terminal Codex startup failure", str(raised.exception))

    def test_worker_and_reviewer_receive_ignore_user_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = controller.RuntimeConfig(
                codex_command=("codex",),
                worker_model="worker",
                reviewer_model="reviewer",
                worker_timeout=1,
                reviewer_timeout=1,
                state_home=root / "state",
                rtk_path=None,
            )
            runner = controller.CodexRunner(config, root, root / "run")
            with mock.patch.object(controller.os, "name", "nt"):
                worker_cmd = runner._base_exec("workspace-write", "worker", "high")
                reviewer_cmd = runner._base_exec("read-only", "reviewer", "medium")
            for cmd in (worker_cmd, reviewer_cmd):
                self.assertEqual(cmd.count("--ignore-user-config"), 1)
                self.assertLess(cmd.index("exec"), cmd.index("--ignore-user-config"))
                self.assertEqual(cmd.count('windows.sandbox="elevated"'), 1)
                self.assertNotIn('windows.sandbox="unelevated"', cmd)
                self.assertLess(cmd.index('windows.sandbox="elevated"'), cmd.index("exec"))

            (root / "run").mkdir()
            captured_resume: list[str] = []

            def fake_run(
                args: object,
                prompt: str,
                timeout: int,
                label: str,
                worker_process: bool = False,
            ) -> controller.ExecResult:
                captured_resume.extend(args)  # type: ignore[arg-type]
                output_path = Path(args[args.index("--output-last-message") + 1])  # type: ignore[index]
                output_path.write_text(
                    json.dumps(
                        {
                            "status": "COMPLETED",
                            "summary": "resumed",
                            "checks": [],
                            "gate": None,
                            "failure": None,
                        }
                    ),
                    encoding="utf-8",
                )
                events = json.dumps({"type": "thread.started", "thread_id": "session-123"}) + "\n"
                return controller.ExecResult(tuple(args), 0, events, "", False)  # type: ignore[arg-type]

            with mock.patch.object(controller.os, "name", "nt"), mock.patch.object(
                runner, "_run_codex", side_effect=fake_run
            ):
                runner.worker_resume("session-123", "continue safely")

            self.assertEqual(captured_resume.count("--ignore-user-config"), 1)
            self.assertLess(captured_resume.index("exec"), captured_resume.index("--ignore-user-config"))
            self.assertLess(captured_resume.index("--ignore-user-config"), captured_resume.index("resume"))
            self.assertEqual(captured_resume.count('windows.sandbox="elevated"'), 1)
            self.assertNotIn('windows.sandbox="unelevated"', captured_resume)

    def test_non_windows_commands_do_not_include_windows_sandbox_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = controller.RuntimeConfig(
                codex_command=("codex",),
                worker_model="worker",
                reviewer_model="reviewer",
                worker_timeout=1,
                reviewer_timeout=1,
                state_home=root / "state",
                rtk_path=None,
            )
            runner = controller.CodexRunner(config, root, root / "run")
            with mock.patch.object(controller.os, "name", "posix"):
                cmd = runner._base_exec("workspace-write", "worker", "high")
            self.assertNotIn('windows.sandbox="elevated"', cmd)
            self.assertNotIn('windows.sandbox="unelevated"', cmd)

    def test_codex_config_change_during_execution_requires_human(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            config_path = codex_home / "config.toml"
            config_path.write_text("before = true\n", encoding="utf-8")
            config = controller.RuntimeConfig(
                codex_command=("codex",),
                worker_model="worker",
                reviewer_model="reviewer",
                worker_timeout=1,
                reviewer_timeout=1,
                state_home=root / "state",
                rtk_path=None,
            )
            runner = controller.CodexRunner(config, root, root / "run")

            def mutate_config(*args: object, **kwargs: object) -> controller.ExecResult:
                config_path.write_text("after = true\n", encoding="utf-8")
                return controller.ExecResult(("codex",), 0, "", "", False)

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False), mock.patch.object(
                controller, "run_process", side_effect=mutate_config
            ):
                with self.assertRaises(controller.HumanRequired):
                    runner._run_codex(["codex"], "", 1, "Worker")

    def test_prompt_hash_change_is_detected_deterministically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt = root / "TASK.md"
            prompt.write_text("before\n", encoding="utf-8")
            state = {
                "branch": "main",
                "initial_head": "abc",
                "prompt_path": str(prompt),
                "prompt_hash": controller.file_sha256(prompt),
            }
            prompt.write_text("after\n", encoding="utf-8")
            snapshot = controller.GitSnapshot(
                root=root,
                branch="main",
                head="abc",
                changed_files=("TASK.md",),
                diff_stat="TASK.md | 2 +-","
            "workspace_fingerprint="synthetic",
            )
            with self.assertRaises(controller.HumanRequired) as raised:
                controller.check_snapshot_invariants(snapshot, state, controller.Policy())
            self.assertIn("prompt/SPEC changed", str(raised.exception))

    def test_process_is_alive_for_current_process(self):
        self.assertTrue(controller.process_is_alive(os.getpid()))

    def test_non_git_target_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            env = os.environ.copy()
            env.update(
                {
                    "ENGINEERING_CONTROLLER_CODEX_CMD": json.dumps([sys.executable, str(FAKE_CODEX)]),
                    "ENGINEERING_CONTROLLER_STATE_HOME": str(base / "state"),
                    "EC_FAKE_SCENARIO": "completed",
                    "EC_FAKE_STATE": str(base / "fake.json"),
                }
            )
            (base / "TASK.md").write_text("task\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(CONTROLLER), "execute", "TASK.md"],
                cwd=str(base),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Git command failed", result.stdout)

    def test_missing_codex_is_detected(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(controller.shutil, "which", return_value=None):
            with self.assertRaises(controller.ValidationError):
                controller.discover_codex_command()

    def test_rtk_is_optional(self):
        fake_cmd = json.dumps([sys.executable, str(FAKE_CODEX)])
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "ENGINEERING_CONTROLLER_CODEX_CMD": fake_cmd,
                "ENGINEERING_CONTROLLER_STATE_HOME": str(Path(temp) / "state"),
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                controller.shutil, "which", return_value=None
            ):
                config = controller.build_runtime_config()
                self.assertIsNone(config.rtk_path)

    def test_state_home_inside_project_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaises(controller.ValidationError):
                controller.StateStore(root / ".controller-state", root)

    def test_worker_schema_accepts_project_extension_risk_flag(self):
        schema = controller.load_schema(controller.WORKER_SCHEMA_PATH)
        payload = {
            "status": "GATE_REQUIRED",
            "summary": "gate",
            "checks": [],
            "gate": {
                "type": "CUSTOM",
                "key": "custom-key",
                "reason": "custom",
                "proposed_action": "safe custom action",
                "risk_flags": ["PROJECT_CUSTOM_RISK"],
                "evidence": [],
            },
            "failure": None,
        }
        controller.validate_json_schema(payload, schema)

    def test_worker_schema_rejects_invalid_status(self):
        schema = controller.load_schema(controller.WORKER_SCHEMA_PATH)
        payload = {"status": "OK", "summary": "x", "checks": [], "gate": None, "failure": None}
        with self.assertRaises(controller.ValidationError):
            controller.validate_json_schema(payload, schema)

    def test_global_forbidden_patterns_cover_core_git_guards(self):
        forbidden = [
            "git push --force origin main",
            "git commit -am test",
            "git reset --hard HEAD~1",
            "git clean -fd",
            "git branch -D old",
            "git merge feature",
            "git checkout main",
            "git restore file.txt",
            "codex --yolo exec task",
        ]
        for command in forbidden:
            with self.subTest(command=command):
                self.assertIsNotNone(controller.forbidden_command_reason(command, []))

    def test_core_remains_project_agnostic(self):
        text = CONTROLLER.read_text(encoding="utf-8").casefold()
        for project_specific_term in ["zabbix", "glpi", "oracle", "senior", "sql_extractor"]:
            with self.subTest(term=project_specific_term):
                self.assertNotIn(project_specific_term, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
