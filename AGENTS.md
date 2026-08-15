# AGENTS.md — engineering-controller

## Source of truth

The normative source for v0.1 is:

`docs/SPEC-v0.1.md`

If any implementation choice conflicts with the SPEC, the SPEC wins unless explicitly amended by the project owner.

## Core constraint: project agnosticism

This repository implements a generic controller. Do not add domain-specific knowledge to the core.

The implementation must not assume any particular:

- programming language;
- framework;
- database;
- cloud/provider;
- application;
- product;
- vendor;
- test framework;
- build tool;
- business domain.

Domain behavior must come from the target project's prompt/SPEC or optional project policy.

## v0.1 scope discipline

Prefer the smallest implementation that satisfies the SPEC.

Use Python standard library whenever practical.

Do not introduce, unless the SPEC is explicitly changed:

- external Python dependencies;
- database;
- HTTP API;
- web UI;
- daemon/service;
- Docker;
- MCP;
- message queue;
- generic agent framework;
- paid OpenAI API integration.

## Safety invariants

Never implement or recommend automatic use of:

- `--dangerously-bypass-approvals-and-sandbox`;
- `--yolo`;
- force push;
- `git reset --hard`;
- destructive `git clean`;
- branch deletion;
- automatic merge.

The v0.1 Worker must not auto-commit or auto-push.

Hard Guards must override Reviewer approval.

Project-specific policies may only restrict global behavior; they may never weaken global protections.

## Implementation workflow

When implementing v0.1:

1. read the SPEC;
2. keep changes inside the authorized branch/worktree;
3. implement in small reviewable increments;
4. add/update tests with each behavior;
5. run focused tests while iterating;
6. run the full required suite at delivery gates;
7. report deviations instead of silently expanding scope.

## Context budget

Keep repository exploration targeted.

Prefer:

- locate/search before opening large files;
- relevant excerpts over full dumps;
- `git diff --stat` before full diff;
- focused test output;
- only changed/relevant files when reviewing.

Context economy must never remove evidence required for a technical or safety decision.
