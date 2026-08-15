# engineering-controller — SPEC v0.1

**Status:** aprovada para implementação  
**Versão:** 0.1  
**Tipo:** user-level Codex Skill  
**Escopo:** genérico para qualquer repositório Git, independentemente de linguagem, framework, produto, fornecedor ou domínio

## 1. Objetivo

Criar uma Codex Skill chamada `engineering-controller` que automatize um Engineering Loop entre duas execuções independentes do Codex CLI:

1. **Worker** — implementa a tarefa autorizada, valida o resultado e retorna um protocolo estruturado.
2. **Reviewer** — revisa somente o gate necessário, em execução independente e sem modificar o projeto.
3. **Controller** — script Python determinístico que orquestra Worker e Reviewer, aplica proteções, controla estado, iterações e retomada.

Fluxo desejado:

```text
$engineering-controller execute <PROMPT_OR_SPEC>
        │
        ▼
     PREFLIGHT
        │
        ▼
      WORKER
        │
        ├── COMPLETED ─────────────► FINAL_VALIDATION ─► COMPLETED
        │
        ├── FAILED ────────────────► FAILED
        │
        └── GATE_REQUIRED
                │
                ▼
            HARD GUARDS
                │
                ├── bloqueado ─────► HUMAN_REQUIRED
                ▼
              REVIEW
                │
                ├── APPROVE ───────► WORKER (resume)
                ├── REVISE ────────► WORKER (resume)
                └── HUMAN_REQUIRED ► STOP
```

A finalidade não é maximizar autonomia. É:

- automatizar trabalho mecânico;
- reduzir copiar/colar entre ferramentas;
- preservar revisão independente;
- reduzir consumo de contexto;
- manter rastreabilidade;
- bloquear operações inseguras;
- chamar o humano apenas quando necessário.

## 2. Agnosticismo obrigatório

O core da skill **não pode conter conhecimento específico de projeto**.

É proibido codificar no core regras dependentes de:

- linguagem de programação;
- framework;
- banco de dados específico;
- fornecedor;
- produto;
- aplicação;
- sistema operacional do projeto alvo;
- nomenclaturas internas de uma empresa;
- estrutura de diretórios de um projeto particular;
- ferramentas de build específicas;
- tecnologia de testes específica;
- domínio funcional específico.

O domínio entra exclusivamente por:

1. prompt/SPEC informado pelo usuário;
2. arquivos do PROJECT TARGET;
3. política opcional do próprio projeto.

A skill pode exigir **Git, Python e Codex CLI** como pré-requisitos operacionais. Isso não é acoplamento de domínio; é parte da infraestrutura do controller.

### 2.1 Política por projeto

Um projeto poderá opcionalmente fornecer:

```text
.engineering-controller-policy.json
```

Essa política só poderá **restringir** o comportamento global.

Ela poderá, por exemplo:

- adicionar caminhos protegidos;
- adicionar comandos proibidos;
- adicionar categorias que exigem `HUMAN_REQUIRED`;
- reduzir limites de iteração.

Ela nunca poderá:

- desabilitar Hard Guards globais;
- permitir bypass de sandbox;
- autorizar Git destrutivo proibido globalmente;
- ampliar permissões do Worker;
- transformar ação globalmente proibida em automática.

Regra:

```text
GLOBAL POLICY + PROJECT POLICY = resultado mais restritivo
```

## 3. Instalação e descoberta

A skill será instalada no escopo de usuário do Codex:

```text
$HOME/.agents/skills/engineering-controller/
```

A instalação não deverá exigir cópia da skill para cada repositório alvo.

A skill deverá ser explicitamente invocada pelo usuário.

Forma inicial:

```text
$engineering-controller execute <PROMPT_OR_SPEC>
```

Retomada:

```text
$engineering-controller resume
```

`status` fica fora da v0.1.

## 4. Invocação explícita

A skill não pode assumir controle de tarefas comuns do Codex.

`agents/openai.yaml` deverá impedir invocação implícita:

```yaml
policy:
  allow_implicit_invocation: false
```

## 5. Estrutura definitiva

```text
engineering-controller/
│
├── SKILL.md
├── agents/
│   └── openai.yaml
│
├── scripts/
│   └── controller.py
│
├── schemas/
│   ├── worker-result.schema.json
│   ├── reviewer-result.schema.json
│   └── project-policy.schema.json
│
├── references/
│   ├── worker-policy.md
│   ├── reviewer-policy.md
│   ├── context-budget.md
│   └── human-required-policy.md
│
└── tests/
    ├── test_controller.py
    └── fakes/
        └── fake_codex.py
```

KISS/YAGNI:

- sem wrapper PowerShell obrigatório;
- sem banco;
- sem API;
- sem daemon;
- sem serviço Windows;
- sem Docker;
- sem MCP;
- sem framework externo de agentes;
- sem dependência Python de terceiros na v0.1.

## 6. Responsabilidades

### 6.1 SKILL.md

Deve permanecer compacto e atuar como interface da skill.

Responsabilidades:

- explicar operações suportadas;
- validar intenção explícita;
- encaminhar `execute` ou `resume` ao Controller;
- apontar para referências auxiliares;
- não duplicar toda a política interna.

### 6.2 Controller

É a autoridade determinística do workflow.

Responsabilidades:

- descobrir PROJECT TARGET;
- validar preflight;
- chamar `codex exec`;
- capturar session ID do Worker;
- validar JSON Schema;
- aplicar Hard Guards;
- chamar Reviewer quando necessário;
- controlar loops;
- persistir estado;
- executar final validation;
- interromper em `HUMAN_REQUIRED`;
- produzir saída terminal curta.

### 6.3 Worker

O Worker:

- pode modificar somente o workspace autorizado;
- implementa a tarefa recebida;
- executa testes e validações apropriados ao projeto;
- não recebe SQL, framework ou stack hardcoded pelo controller;
- retorna sempre resultado compatível com o Worker Schema;
- não executa Git destrutivo;
- não executa push, merge ou commit automático na v0.1;
- não usa bypass de sandbox.

### 6.4 Reviewer

O Reviewer:

- roda em execução Codex separada;
- recebe contexto significativamente menor;
- usa sandbox `read-only`;
- não edita arquivos;
- não faz commit;
- não faz push;
- não altera Git;
- não altera dados externos;
- avalia apenas o gate;
- retorna `APPROVE`, `REVISE` ou `HUMAN_REQUIRED`.

## 7. Dependências

Obrigatórias:

- Python 3;
- Git CLI;
- Codex CLI autenticado.

Recomendada:

- RTK.

A ausência de RTK gera `WARN`, não `FAIL`.

Preferir Python standard library:

- `subprocess`;
- `pathlib`;
- `json`;
- `dataclasses` se útil;
- `tempfile`;
- `datetime`;
- `logging`;
- `hashlib`;
- `shutil`.

## 8. Preflight

Antes do Worker, o Controller deve verificar:

1. Git disponível;
2. Codex CLI disponível;
3. projeto dentro de repositório Git;
4. raiz Git identificável;
5. branch atual identificável;
6. HEAD inicial identificável;
7. working tree limpa;
8. prompt/SPEC solicitado existe;
9. prompt/SPEC está dentro do PROJECT TARGET;
10. nenhuma execução conflitante existe para o mesmo projeto;
11. schemas internos podem ser carregados;
12. RTK detectado, se disponível.

Resultados mínimos:

```text
Git ausente             → FAILED
Codex ausente           → FAILED
Projeto não Git         → FAILED
Prompt inexistente      → FAILED
Prompt fora do projeto  → FAILED
Detached HEAD           → HUMAN_REQUIRED
Working tree suja       → HUMAN_REQUIRED
Execução concorrente    → HUMAN_REQUIRED
RTK ausente             → WARN
```

## 9. Git Safety

Antes da primeira execução registrar:

- `project_root`;
- branch;
- HEAD inicial;
- working tree inicial.

Na v0.1:

- Worker não faz commit automático;
- Worker não faz push;
- Controller não faz merge;
- Controller não apaga branch;
- Controller não executa rollback destrutivo.

Invariante principal:

```text
HEAD_FINAL == HEAD_INICIAL
```

Operações proibidas incluem, sem limitar:

```text
git push --force
git push -f
git reset --hard
git clean -f
git clean -fd
git branch -D
git checkout que descarte trabalho
git restore usado para descartar mudanças preexistentes
merge automático
branch deletion
```

Se branch ou HEAD mudarem inesperadamente durante o ciclo:

```text
HUMAN_REQUIRED
```

## 10. Execução do Worker

Configuração conceitual:

```text
codex exec
-C <PROJECT_ROOT>
--model <worker-model>
--sandbox workspace-write
--ask-for-approval never
--json
--output-schema <worker-schema>
--output-last-message <worker-output>
-
```

O prompt deverá preferencialmente ser enviado por `stdin`.

Reasoning inicial:

```text
high
```

A v0.1 não altera permanentemente `~/.codex/config.toml`.

### 10.1 Continuação

O Controller captura `thread_id` da primeira execução Worker.

Após `APPROVE` ou `REVISE`, deve retomar o mesmo Worker:

```text
codex exec resume <WORKER_SESSION_ID> <FOLLOW_UP>
```

O follow-up deve conter somente o contexto novo necessário.

## 11. Política de aprovação

A v0.1 deve usar:

```text
--ask-for-approval never
```

Não utilizar `--approve-for-me` na v0.1.

É expressamente proibido construir, aceitar ou sugerir automaticamente:

```text
--dangerously-bypass-approvals-and-sandbox
--yolo
```

Qualquer solicitação que dependa de escalonamento fora da política deve falhar fechado ou resultar em `HUMAN_REQUIRED`, conforme a natureza do gate.

## 12. Execução do Reviewer

Cada gate cria uma execução Reviewer nova e independente.

Configuração conceitual:

```text
codex exec
-C <PROJECT_ROOT>
--model <reviewer-model>
--sandbox read-only
--ask-for-approval never
--ephemeral
--json
--output-schema <reviewer-schema>
--output-last-message <reviewer-output>
-
```

Reasoning inicial:

```text
medium
```

O Reviewer não será retomado entre gates.

## 13. Worker Result Protocol

Estados permitidos:

```text
COMPLETED
GATE_REQUIRED
FAILED
```

Campos normativos:

```json
{
  "status": "COMPLETED | GATE_REQUIRED | FAILED",
  "summary": "string curta",
  "checks": [],
  "gate": null,
  "failure": null
}
```

Quando `status = GATE_REQUIRED`, `gate` deve conter:

```json
{
  "type": "string",
  "key": "string estável",
  "reason": "string",
  "proposed_action": "string",
  "risk_flags": [],
  "evidence": []
}
```

O JSON Schema completo será implementado em `schemas/worker-result.schema.json`.

### 13.1 Campos calculados pelo Controller

Não confiar no modelo para informações que Git pode fornecer deterministicamente.

Portanto, o Worker Schema não precisa carregar como fonte de verdade:

- branch atual;
- HEAD;
- lista de arquivos alterados;
- diff stat.

Esses valores serão calculados pelo Controller.

## 14. Reviewer Result Protocol

Decisões permitidas:

```text
APPROVE
REVISE
HUMAN_REQUIRED
```

Contrato conceitual:

```json
{
  "decision": "APPROVE | REVISE | HUMAN_REQUIRED",
  "reason": "string",
  "instructions": "string",
  "risk_flags": []
}
```

Não haverá booleano `human_required`, pois seria redundante.

## 15. Taxonomia de risco global

A taxonomia do core deve ser genérica.

Flags iniciais:

```text
DESTRUCTIVE_CHANGE
SECURITY_WEAKENING
CREDENTIAL_OR_SECRET
ARCHITECTURE_CHANGE
SCOPE_INCREASE
BUSINESS_BEHAVIOR_CHANGE
COST_INCREASE
PRODUCTION_RISK
OUTSIDE_SPEC
PROTECTED_AREA
GIT_DESTRUCTIVE
BYPASS_SANDBOX
```

Projetos podem adicionar classificações próprias por política local, mas o core não deve incorporar categorias específicas de um único domínio.

## 16. HUMAN_REQUIRED

Existem duas camadas.

### 16.1 Hard Guards determinísticos

O Controller interrompe independentemente da opinião do Reviewer quando detectar:

- working tree inesperadamente suja no início;
- detached HEAD;
- branch alterada durante o ciclo;
- HEAD alterado inesperadamente;
- force push;
- reset destrutivo;
- limpeza destrutiva;
- branch deletion;
- merge automático;
- bypass de sandbox;
- tentativa de alteração de caminho protegido;
- alteração de secret conhecido;
- conflito de execução;
- limite de loops atingido;
- alteração fora da raiz autorizada.

Reviewer não pode sobrepor Hard Guard.

### 16.2 Semantic Guards

Algumas decisões são semânticas e não podem ser inferidas com segurança apenas por Python.

Exemplos genéricos:

- mudança material de arquitetura;
- aumento material de escopo;
- mudança funcional ou de negócio;
- aumento material de custo;
- alteração de mecanismo de segurança;
- risco operacional em produção;
- trabalho fora da SPEC;
- alteração de área protegida.

Mecanismo:

1. Worker classifica `risk_flags`;
2. Reviewer classifica `risk_flags`;
3. Controller calcula a união;
4. flags configuradas como humanas forçam `HUMAN_REQUIRED`;
5. isso prevalece mesmo se Reviewer responder `APPROVE`.

A decisão final é determinística; a classificação semântica é model-assisted.

### 16.3 Exemplos de operações que devem exigir humano

A lista abaixo é ilustrativa e cross-domain:

- modificação destrutiva de dados;
- operação destrutiva em ambiente produtivo;
- remoção ou enfraquecimento de proteção;
- exposição ou alteração de secrets;
- alteração de credenciais;
- alteração material de arquitetura;
- aumento material de escopo;
- mudança funcional/de negócio;
- aumento material de custo;
- risco operacional em produção;
- alteração fora da SPEC;
- alteração de núcleo/área protegida;
- Git destrutivo;
- qualquer bypass de sandbox/approvals.

Operações específicas de uma tecnologia, como DML/DDL produtivo, migrações destrutivas, exclusões em storage, mudanças em infraestrutura ou equivalentes, são classificadas por essas categorias genéricas e/ou por política do projeto.

## 17. Máquina de estados

Estados persistidos:

```text
PREFLIGHT
WORKER
REVIEW
HUMAN_REQUIRED
FINAL_VALIDATION
COMPLETED
FAILED
```

Não persistir estados redundantes apenas para refletir cada chamada de subprocesso.

Transições devem ser explícitas e determinísticas.

## 18. Controle de loop

Limites iniciais:

```text
MAX_SAME_GATE = 3
MAX_TOTAL_REVIEWS = 6
```

Cada gate deve possuir `type` e `key`.

Fingerprint:

```text
SHA256(gate.type + ":" + gate.key)
```

Após três ciclos do mesmo gate:

```text
HUMAN_REQUIRED
```

Após seis revisões totais, mesmo com gates distintos:

```text
HUMAN_REQUIRED
```

## 19. Context Budget

Economia de contexto é requisito da v0.1.

### 19.1 Worker

Primeira execução recebe:

- prompt/SPEC solicitado;
- worker policy;
- context budget;
- preflight mínimo.

Execuções retomadas recebem apenas:

- decisão Reviewer;
- instruções Reviewer;
- gate atual;
- delta Git relevante;
- informação humana nova, quando aplicável.

Não reenviar automaticamente o prompt completo.

### 19.2 Reviewer

Cada Reviewer recebe apenas:

- reviewer policy;
- human-required policy;
- Worker gate JSON;
- estado Git relevante;
- `git diff --stat`;
- evidências referenciadas;
- trecho aplicável da SPEC;
- diff direcionado quando necessário.

Não enviar automaticamente:

- histórico completo;
- logs completos;
- todas as SPECs;
- todos os arquivos;
- todo o Git history.

### 19.3 Regras operacionais

Worker e Reviewer devem:

- localizar antes de abrir;
- buscar antes de ler arquivos grandes;
- ler trechos relevantes;
- usar `git diff --stat` antes de diff integral;
- priorizar arquivos alterados;
- procurar padrões de erro antes de despejar logs;
- executar testes focais durante implementação;
- executar suíte completa somente quando exigida pelo gate/SPEC;
- não reler baseline sem necessidade;
- evitar exploração aberta quando a SPEC já define o caminho.

Regra superior:

**economia de tokens nunca pode remover evidência necessária para decisão técnica ou de segurança.**

## 20. RTK

RTK é opcional.

Se disponível, Worker e Reviewer devem preferi-lo quando houver equivalente seguro e a redução de contexto não eliminar evidência necessária.

Preflight:

```text
RTK disponível → INFO
RTK ausente    → WARN
```

O Controller não depende funcionalmente de RTK.

## 21. Persistência

Runtime state não deve ficar dentro do PROJECT TARGET.

Diretório padrão:

```text
$HOME/.engineering-controller/
```

Estrutura:

```text
.engineering-controller/
└── runs/
    └── <repo-hash>/
        ├── current.json
        └── <run-id>/
            ├── state.json
            ├── worker-result.json
            ├── reviewer-result.json
            └── run.log
```

`repo-hash` deriva do caminho absoluto normalizado da raiz Git.

## 22. Estado mínimo

`state.json` deve manter somente o necessário para rastreabilidade e resume:

```text
schema_version
run_id
status
project_root
prompt_path
prompt_hash
branch
initial_head
worker_session_id
worker_runs
review_count
gate_counts
current_gate
last_review
started_at
updated_at
```

Não persistir:

- tokens;
- passwords;
- API keys;
- cookies;
- conteúdo de `.env`;
- secrets sem necessidade.

## 23. Resume

`resume` faz parte da v0.1.

Fluxo:

```text
$engineering-controller resume
```

O Controller deve:

1. identificar PROJECT TARGET atual;
2. localizar `current.json` correspondente;
3. confirmar estado `HUMAN_REQUIRED`;
4. validar branch;
5. recalcular Git state;
6. identificar mudanças feitas pelo humano;
7. atualizar state;
8. enviar continuação mínima ao Worker original;
9. usar `codex exec resume <worker_session_id>`.

Se o Worker original não puder ser retomado:

```text
FAILED
```

A v0.1 não criará silenciosamente um novo Worker fingindo possuir o contexto anterior.

## 24. Logs

Saída terminal deve ser curta:

```text
[EC] Preflight OK
[EC] Worker #1 started
[EC] Gate: <TYPE>
[EC] Reviewer #1: REVISE
[EC] Worker #2 resumed
[EC] COMPLETED
```

Ou:

```text
[EC] HUMAN_REQUIRED
Reason: <reason>
State saved for resume.
```

Log local deve registrar:

- timestamp;
- run ID;
- project root;
- branch;
- HEAD inicial;
- Worker run;
- Worker status;
- gate;
- Reviewer run;
- decisão;
- iteração;
- duração;
- status final.

Não registrar secrets.

## 25. Final Validation

`COMPLETED` do Worker não encerra diretamente o Controller.

Verificar:

```text
branch atual == branch inicial
HEAD atual == HEAD inicial
mudanças permanecem dentro do project root
nenhum Hard Guard foi violado
Worker final JSON é válido
Worker final status == COMPLETED
estado persistente é consistente
```

Testes de domínio continuam responsabilidade do Worker, pois o Controller é agnóstico de stack.

Reviewer não é chamado quando Worker retorna diretamente `COMPLETED`.

## 26. Tratamento de falhas

Não fazer retry cego.

Categorias:

### PROCESS

Exemplos:

- Codex não inicia;
- timeout;
- processo termina inesperadamente.

Resultado: `FAILED`.

### PROTOCOL

Exemplos:

- JSON inválido;
- Schema inválido;
- thread ID ausente;
- arquivo de output ausente.

Resultado: `FAILED`.

### TASK

Worker executou corretamente, mas a tarefa não pode ser concluída.

Resultado: `FAILED` ou `GATE_REQUIRED`, conforme contrato.

### POLICY

Hard Guard ou decisão humana obrigatória.

Resultado: `HUMAN_REQUIRED`.

## 27. Timeouts

Valores iniciais:

```text
WORKER_TIMEOUT   = 30 minutos por execução
REVIEWER_TIMEOUT = 10 minutos por execução
```

Timeout não provoca retry automático.

## 28. Testes obrigatórios

### 28.1 Unitários sem consumo real de Codex

Usar Python standard library e fake CLI.

Cobrir:

- projeto não Git;
- prompt inexistente;
- prompt fora do projeto;
- dirty tree;
- detached HEAD;
- Codex ausente;
- RTK ausente;
- JSON Worker inválido;
- JSON Reviewer inválido;
- exit code Worker != 0;
- exit code Reviewer != 0;
- timeout Worker;
- timeout Reviewer;
- state corrompido;
- resume sem estado;
- branch alterada;
- HEAD alterado;
- limite do mesmo gate;
- limite global;
- Git proibido;
- bypass de sandbox;
- secret path;
- Reviewer `APPROVE` com flag que força humano.

### 28.2 Teste A — COMPLETED

```text
Worker → COMPLETED → FINAL_VALIDATION → COMPLETED
```

Reviewer não chamado.

### 28.3 Teste B — GATE + REVISE

```text
Worker → GATE_REQUIRED → Reviewer REVISE → Worker resume → COMPLETED
```

### 28.4 Teste C — GATE + APPROVE

```text
Worker → GATE_REQUIRED → Reviewer APPROVE → Worker resume → COMPLETED
```

### 28.5 Teste D — HUMAN_REQUIRED

Simular ação proibida, por exemplo Git destrutivo ou bypass de sandbox.

Esperado:

```text
Controller → HUMAN_REQUIRED
Reviewer → não executado quando Hard Guard já for suficiente
```

### 28.6 Integração real com Codex

Após unitários verdes:

- repositório temporário;
- Worker real;
- Reviewer real;
- validar `--output-schema`;
- validar `--json`;
- capturar `thread_id`;
- validar `codex exec resume`;
- validar Reviewer read-only.

### 28.7 Integração da Skill

Validar explicitamente:

```text
codex
> $engineering-controller execute <synthetic-prompt>
```

Critérios:

1. skill encontrada;
2. não disparada implicitamente;
3. script Python executável;
4. script consegue iniciar subprocessos `codex exec`;
5. autenticação reutilizada;
6. Worker e Reviewer independentes.

## 29. Riscos conhecidos

### 29.1 Nested Codex

O caso Skill → script Python → subprocesso `codex exec` deve ser provado por teste de integração antes do primeiro piloto real.

### 29.2 Escrita dentro do workspace

`workspace-write` permite mudanças no projeto. Git safety e working tree limpa reduzem risco, mas não transformam o filesystem em uma transação.

### 29.3 Efeitos externos

Um projeto pode executar comandos que interagem com serviços, infraestrutura, storage ou dados externos.

O Controller não substitui permissões reais do ambiente. Least privilege continua obrigatório no sistema externo.

### 29.4 Classificação semântica

Mudança de arquitetura, escopo, negócio ou produção exige interpretação. A decisão final é enforced pelo Controller, porém a classificação é model-assisted.

### 29.5 Consumo

Cada Worker e Reviewer é uma execução real do Codex. Limites de gate/review existem para impedir loops indefinidos.

## 30. Fora de escopo da v0.1

Não criar:

- interface web;
- API HTTP;
- banco de dados;
- SQLite;
- Docker;
- Redis;
- fila;
- daemon;
- serviço Windows;
- Kubernetes;
- MCP;
- integração Slack/Teams;
- GitHub App;
- webhook;
- dashboard;
- telemetria externa;
- OpenAI API paga;
- RAG;
- embeddings;
- vector database;
- framework genérico de agentes;
- plugin de IDE;
- execução distribuída;
- auto-commit;
- auto-push;
- auto-PR;
- auto-merge;
- múltiplos Reviewers paralelos;
- múltiplos Workers paralelos.

## 31. Critérios de aceite

A v0.1 só é considerada pronta quando:

1. estrutura user-level válida;
2. invocação implícita desabilitada;
3. `execute` funcional;
4. `resume` funcional;
5. nested `codex exec` comprovado;
6. Worker separado;
7. Reviewer separado e read-only;
8. Worker retomável por session ID;
9. JSON Schemas validados;
10. JSON inválido falha fechado;
11. preflight Git funcional;
12. dirty tree bloqueia por padrão;
13. bypass perigoso nunca é gerado;
14. `--approve-for-me` não é usado;
15. Hard Guards prevalecem sobre Reviewer;
16. limites de loop funcionam;
17. estado sobrevive a interrupção;
18. RTK ausente gera somente WARN;
19. logs não registram secrets;
20. unit tests verdes;
21. testes A/B/C/D verdes;
22. integração real Codex verde;
23. nenhuma alteração permanente em config global do Codex;
24. nenhuma regra de domínio específico incorporada ao core;
25. política de projeto só consegue restringir, nunca ampliar permissões.

## 32. Regra de evolução

Qualquer feature futura deve responder primeiro:

> Essa capacidade pertence ao Controller genérico ou é uma regra do projeto alvo?

Se a resposta for “regra do projeto”, ela deve permanecer fora do core e entrar por prompt/SPEC ou policy local.

Esse princípio é obrigatório para preservar o agnosticismo do `engineering-controller`.
