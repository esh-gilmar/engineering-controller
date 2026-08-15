---
name: engineering-controller
description: Explicitly run a controlled Codex Worker ↔ Reviewer engineering loop for a prompt/SPEC file in the current Git repository. Use only when the user explicitly mentions $engineering-controller; do not use for normal coding tasks.
---

# Engineering Controller

Use this skill only after explicit `$engineering-controller` invocation.

Do not silently convert an ordinary Codex task into this workflow.

## Supported operations

### Execute

User form:

```text
$engineering-controller execute <PROMPT_OR_SPEC>
```

Run the deterministic controller from the directory where the user invoked the skill, preserving that directory as the PROJECT TARGET context:

```text
python -X utf8 <SKILL_ROOT>/scripts/controller.py execute <PROMPT_OR_SPEC>
```

`<SKILL_ROOT>` is the directory containing this `SKILL.md`.

The prompt/SPEC must be a file inside the PROJECT TARGET. It is Controller input, not Worker output. It may be tracked/clean, tracked/modified, or untracked; only that exact input path is exempted from the initial dirty-worktree gate. Any other pre-existing change remains blocking.

Do not implement the target task yourself before or alongside the Controller. The Controller starts separate Worker and Reviewer Codex executions and owns the state machine.

### Resume

User form:

```text
$engineering-controller resume
```

or, when the human supplies an explicit resolution:

```text
$engineering-controller resume <human resolution note>
```

Run:

```text
python -X utf8 <SKILL_ROOT>/scripts/controller.py resume [human resolution note]
```

Do not invent a human approval note. Forward only what the user actually supplied.

`resume` is valid only when the Controller reports that the saved run is resumable, or when it detects and safely recovers a stale/orphaned `WORKER` run with the same saved Worker session. If the Controller says no resumable run was created, no Worker session was persisted, or an execution is still active, do not promise or retry `resume`; present the Controller's recovery instruction exactly.

## Output handling

Present the Controller's concise terminal result to the user.

Exit meanings:

- `0` — `COMPLETED`;
- `1` — `FAILED`;
- `2` — `HUMAN_REQUIRED`;
- `130` — interrupted.

When `HUMAN_REQUIRED`, stop the automated loop. Do not work around the gate in the parent Codex session. Recommend `$engineering-controller resume` only when the Controller explicitly says `resume` is available for that saved execution.

When the Controller reports an active `WORKER`, do not launch another Worker. When it reports a stale `WORKER` without a persisted Worker session, preserve the existing workspace delivery and follow the explicit controlled-recovery instruction; never edit Controller state files manually.

## Non-negotiable safety

The v0.1 Controller does not auto-commit, auto-push, auto-merge, discard work, or bypass sandbox/approval protections. When a protected operation is required, stop at `HUMAN_REQUIRED`.

## Progressive disclosure

The Controller loads detailed policy from:

- `references/worker-policy.md`;
- `references/reviewer-policy.md`;
- `references/context-budget.md`;
- `references/human-required-policy.md`.

The normative project specification is `docs/SPEC-v0.1.md` in the engineering-controller source repository. Do not copy that entire specification into the target project's context unless required for maintenance of the skill itself.
