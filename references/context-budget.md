# Context Budget — engineering-controller v0.1

Context efficiency is a design constraint, but evidence required for correctness or safety always wins over token savings.

## General rules

- Locate before reading.
- Search before opening a large file.
- Read relevant excerpts instead of full documents when possible.
- Do not reread a baseline already established in the current Worker session.
- Prefer `git diff --stat` before a full diff.
- Inspect changed and evidence-referenced files first.
- Search logs for `ERROR`, `WARN`, failure names, identifiers, or other targeted patterns before dumping complete logs.
- Use focused tests while implementing; run the complete required suite only at delivery gates defined by the project.
- Avoid open-ended repository exploration when the prompt/SPEC already identifies the relevant area.
- Keep structured results short and point to evidence rather than copying it.

## Worker

The first Worker turn receives the governing prompt/SPEC plus the Worker policy and this Context Budget.

A resumed Worker should receive only new information:

- Reviewer decision;
- Reviewer instructions;
- current gate identity;
- current Git summary;
- human-provided resolution when resuming after `HUMAN_REQUIRED`.

Do not resend the complete original prompt on every resume; the Worker session already owns that context.

## Reviewer

Each Reviewer is fresh and independent.

It should receive only:

- Reviewer policy;
- Human Required policy;
- current Worker gate JSON;
- project prompt/SPEC path;
- current Git branch/HEAD/diff stat;
- evidence references;
- optional restrictive project policy.

The Reviewer can read targeted PROJECT TARGET files in read-only mode when needed. Do not automatically send complete logs, complete repository history, old SPECs, or every changed file's full contents.

## RTK

If RTK is installed and an equivalent command safely preserves the evidence needed for the decision, prefer RTK to reduce output volume.

RTK is optional. Its absence is a warning, never a reason to fail the Engineering Loop.

## Safety override

Never omit evidence that is necessary to determine correctness, scope, security, destructive impact, production risk, or another safety gate merely to reduce context.
