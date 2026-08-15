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

## Output handling

Present the Controller's concise terminal result to the user.

Exit meanings:

- `0` — `COMPLETED`;
- `1` — `FAILED`;
- `2` — `HUMAN_REQUIRED`;
- `130` — interrupted.

When `HUMAN_REQUIRED`, stop the automated loop. Do not work around the gate in the parent Codex session.

## Non-negotiable safety

The v0.1 Controller does not auto-commit, auto-push, auto-merge, discard work, or bypass sandbox/approval protections. When a protected operation is required, stop at `HUMAN_REQUIRED`.

## Progressive disclosure

The Controller loads detailed policy from:

- `references/worker-policy.md`;
- `references/reviewer-policy.md`;
- `references/context-budget.md`;
- `references/human-required-policy.md`.

The normative project specification is `docs/SPEC-v0.1.md` in the engineering-controller source repository. Do not copy that entire specification into the target project's context unless required for maintenance of the skill itself.
