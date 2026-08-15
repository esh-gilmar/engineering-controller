# HUMAN_REQUIRED Policy — engineering-controller v0.1

`HUMAN_REQUIRED` is a safety stop, not an error and not a request to bypass controls.

The Controller must save enough state for a later `resume` and stop automated work.

## Global human-required risk flags

The v0.1 global policy treats these flags as human-required:

- `DESTRUCTIVE_CHANGE`
- `SECURITY_WEAKENING`
- `CREDENTIAL_OR_SECRET`
- `ARCHITECTURE_CHANGE`
- `SCOPE_INCREASE`
- `BUSINESS_BEHAVIOR_CHANGE`
- `COST_INCREASE`
- `PRODUCTION_RISK`
- `OUTSIDE_SPEC`
- `PROTECTED_AREA`
- `GIT_DESTRUCTIVE`
- `BYPASS_SANDBOX`

A project policy may add more flags. It may not remove any global flag.

## Deterministic Hard Guards

The Controller must stop without allowing a Reviewer override when it detects, among other equivalent prohibited operations:

- unexpected dirty working tree before the first Worker;
- detached HEAD;
- unexpected branch change;
- unexpected HEAD change;
- Worker commit or push;
- force push;
- destructive reset/clean/checkout/restore;
- branch deletion or automatic merge;
- sandbox/approval bypass;
- modification of a protected or known-secret path;
- a configured forbidden command;
- execution conflict for the same PROJECT TARGET;
- repeated-gate or total-review limit exhaustion.

When a forbidden command is observed in Codex JSONL after execution, the Controller must still stop and record the policy violation. Detection after the fact does not make the operation safe; the Worker policy and Codex sandbox are the preventive layers.

## Semantic guards

Some materiality judgments are semantic. Worker and Reviewer classify them with risk flags; the Controller deterministically enforces the union of those flags.

If either side returns a human-required flag, the Controller stops even if the Reviewer decision is `APPROVE`.

## Human resolution and resume

The human may inspect the repository, decide the gate, and make safe workspace changes if needed.

`resume` must revalidate:

- same PROJECT TARGET;
- same branch;
- unchanged initial HEAD;
- no protected/secret-path violation;
- valid saved state in `HUMAN_REQUIRED`.

The Controller then resumes the original Worker session with only the new gate resolution context. If the original Worker session cannot be resumed, fail closed rather than silently pretending a fresh Worker has the missing context.
