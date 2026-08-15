# engineering-controller

> **Chega de pombo-correio entre Codex e GPT Web.**
>
> Sabe aquele ritual de `Codex → copiar resposta → colar no GPT Web → copiar revisão → voltar ao Codex → repetir` até alguém perder a paciência? O **engineering-controller** existe para aposentar esse leva-e-traz.

O projeto implementa uma **Codex Skill de usuário** que orquestra um Engineering Loop com três responsabilidades separadas:

- **Worker** — implementa a tarefa e produz gates estruturados.
- **Reviewer** — revisa o gate em uma execução independente e decide `APPROVE`, `REVISE` ou `HUMAN_REQUIRED`.
- **Controller** — motor determinístico em Python que controla estado, segurança, iterações e retomada.

O objetivo não é maximizar autonomia. É automatizar trabalho mecânico, preservar gates de segurança, reduzir consumo de contexto e chamar o humano apenas quando existe uma decisão que realmente exige responsabilidade humana.

## Status

**v0.1 — especificação aprovada para implementação.**

A implementação ainda deve seguir a SPEC vigente em [`docs/SPEC-v0.1.md`](docs/SPEC-v0.1.md).

## Agnóstico por projeto

O `engineering-controller` é intencionalmente **agnóstico de domínio e stack**. Ele não contém conhecimento específico de banco de dados, framework, linguagem, fornecedor, produto ou aplicação.

A skill trata o repositório em que foi invocada como **PROJECT TARGET** e recebe o domínio exclusivamente do prompt/SPEC do próprio projeto.

Isso significa que o mesmo controller deve poder operar, por exemplo, em projetos Python, Node.js, .NET, Java, infraestrutura, documentação ou automação — desde que o alvo seja um repositório Git e cumpra os pré-requisitos operacionais da skill.

Políticas específicas de um projeto podem **restringir** o comportamento do controller, mas nunca enfraquecer suas proteções globais.

## Fluxo conceitual

```text
$engineering-controller execute <PROMPT_OR_SPEC>
                    │
                    ▼
                Preflight
                    │
                    ▼
                  Worker
                    │
          ┌─────────┴─────────┐
          │                   │
      COMPLETED          GATE_REQUIRED
          │                   │
          ▼                   ▼
 Final Validation       Hard Guards
                              │
                              ▼
                           Reviewer
                              │
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
               APPROVE      REVISE   HUMAN_REQUIRED
                  │           │           │
                  └─────► Worker ◄───────┘
                         (resume)          STOP
```

## Princípios

- KISS
- YAGNI
- DRY
- Fail safe
- Least privilege
- Revisão independente
- Context Budget
- Human-in-the-loop somente quando necessário
- Nenhum bypass de sandbox ou aprovações

## Escopo da v0.1

A v0.1 será uma **user-level Codex Skill** com:

- `SKILL.md` compacto;
- invocação explícita;
- `controller.py` em Python standard library;
- Worker e Reviewer executados por processos `codex exec` separados;
- JSON Schemas para os protocolos;
- sandbox `workspace-write` para Worker e `read-only` para Reviewer;
- estado persistente fora do repositório alvo;
- `resume` mínimo para ciclos interrompidos por `HUMAN_REQUIRED`;
- Git safety;
- Context Budget;
- RTK opcional;
- testes unitários, sintéticos e de integração.

## O que a v0.1 não é

Não é um framework genérico de agentes, servidor, daemon, API, dashboard, MCP, banco de dados, pipeline distribuído ou ferramenta de CI/CD.

Também não faz automaticamente merge, push, force push, reset destrutivo, exclusão de branch ou bypass de sandbox.

## Estrutura planejada

```text
engineering-controller/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── controller.py
├── schemas/
│   ├── worker-result.schema.json
│   ├── reviewer-result.schema.json
│   └── project-policy.schema.json
├── references/
│   ├── worker-policy.md
│   ├── reviewer-policy.md
│   ├── context-budget.md
│   └── human-required-policy.md
└── tests/
```

## Dependências da v0.1

Obrigatórias:

- Codex CLI autenticado;
- Python 3;
- Git.

Recomendada:

- RTK, quando houver equivalente seguro que reduza contexto sem perder evidência necessária.

## Segurança

O controller deve falhar fechado quando o protocolo, o estado ou uma operação de segurança não puderem ser validados.

É expressamente proibido ao projeto executar ou recomendar automaticamente:

```text
--dangerously-bypass-approvals-and-sandbox
--yolo
git push --force
git reset --hard
git clean -fd
git branch -D
```

Consulte a SPEC para a política completa.

---

### A frase de elevador

**Um controlador para deixar Codex e Reviewer conversarem entre si sem transformar você no cabo USB humano do processo.**
