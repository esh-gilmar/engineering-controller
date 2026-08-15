# Implementation Plan — engineering-controller v0.1

Este plano organiza a implementação sem ampliar o escopo da SPEC.

## Phase 0 — Foundation

- [x] Inicializar repositório.
- [x] Publicar SPEC v0.1 agnóstica de projeto.
- [x] Publicar `AGENTS.md` com regras de desenvolvimento.
- [x] Adicionar `.gitignore`.
- [x] Documentar objetivo e arquitetura no README.

## Phase 1 — Skill skeleton

Criar somente a estrutura necessária:

```text
SKILL.md
agents/openai.yaml
scripts/controller.py
schemas/
references/
tests/
```

Critérios:

- invocação explícita;
- `allow_implicit_invocation: false`;
- nenhum conhecimento de domínio no core;
- nenhuma dependência externa Python.

## Phase 2 — Protocols and policies

Implementar:

- Worker JSON Schema;
- Reviewer JSON Schema;
- Project Policy JSON Schema;
- Worker policy;
- Reviewer policy;
- Context Budget policy;
- HUMAN_REQUIRED policy.

Critérios:

- schemas enxutos;
- `additionalProperties: false` onde apropriado;
- taxonomia de risco genérica;
- policy local só pode restringir.

## Phase 3 — Deterministic Controller core

Implementar:

- CLI interna `execute` e `resume`;
- descoberta de Git root;
- preflight;
- persistência fora do project target;
- subprocess runner;
- JSONL parsing;
- schema validation usando somente standard library;
- Worker launch;
- Worker resume;
- Reviewer launch;
- state machine;
- Hard Guards;
- loop limits;
- final validation;
- logs seguros.

Critérios:

- fail closed;
- sem auto-commit;
- sem auto-push;
- sem auto-merge;
- sem bypass de sandbox.

## Phase 4 — Synthetic test suite

Implementar `fake_codex.py` e cobrir:

- COMPLETED;
- GATE + REVISE;
- GATE + APPROVE;
- deterministic HUMAN_REQUIRED;
- JSON inválido;
- exit code != 0;
- timeout;
- gate loop limit;
- non-Git target;
- missing prompt;
- dirty worktree;
- missing Codex;
- missing RTK;
- corrupt state;
- resume sem estado;
- interrupted execution.

Todos esses testes devem rodar sem consumir uma execução real do Codex.

## Phase 5 — Real Codex integration

Somente depois dos testes sintéticos estarem verdes:

- validar `codex exec` real;
- validar `--json`;
- validar `--output-schema`;
- validar `--output-last-message`;
- capturar `thread_id`;
- validar `codex exec resume`;
- validar Reviewer `read-only`;
- provar nested Codex: Skill → Controller → `codex exec`.

Esse é um gate obrigatório da v0.1.

## Phase 6 — User-level installation validation

Validar instalação em:

```text
$HOME/.agents/skills/engineering-controller/
```

Testar:

```text
$engineering-controller execute <synthetic-prompt>
```

E:

```text
$engineering-controller resume
```

A skill não pode ser invocada implicitamente em tarefas comuns.

## Phase 7 — Release candidate

Antes de qualquer piloto real:

- full test suite verde;
- revisão de segurança;
- revisão de agnosticismo;
- revisão de Context Budget;
- verificação de ausência de secrets;
- verificação de ausência de dependências externas inesperadas;
- validação final da SPEC.

## Gate de agnosticismo

Em cada fase, revisar:

> Esta lógica é necessária para qualquer PROJECT TARGET ou pertence a um projeto específico?

Se pertencer a um projeto específico, não deve entrar no core. Deve ser fornecida pelo prompt/SPEC do alvo ou por policy local restritiva.
