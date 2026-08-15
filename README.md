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

**v0.1.0 — concluída e validada end-to-end.**

Baseline validado:

```text
276d30bf7048a216137445634c0f87636c64da81
```

Validações realizadas na v0.1.0:

- 36/36 testes sintéticos;
- integração real com Codex CLI;
- Worker real em processo separado;
- Reviewer independente em processo separado e `read-only`;
- `GATE_REQUIRED → APPROVE → resume` da mesma sessão Worker;
- `workspace-write` real;
- validação final e invariantes Git;
- isolamento de `config.toml`;
- instalação como user-level Skill;
- invocação explícita via `$engineering-controller`;
- smoke test real criando somente o arquivo esperado, sem commit ou push automático.

A especificação normativa da versão está em [`docs/SPEC-v0.1.md`](docs/SPEC-v0.1.md).

## How to use

### 1. Pré-requisitos

Obrigatórios:

- Codex CLI autenticado;
- Python 3 funcional no ambiente em que o Codex é iniciado;
- Git;
- projeto alvo em um repositório Git;
- working tree limpa antes de iniciar um novo run.

Recomendado:

- RTK, quando houver equivalente seguro que reduza contexto sem perder evidência necessária.

A v0.1.0 não exige API paga da OpenAI. Os Workers e Reviewers usam a autenticação já disponível no Codex CLI.

### 2. Instalar como user-level Skill

Clone este repositório e, a partir da raiz dele, copie a Skill para o diretório user-level do Codex.

PowerShell:

```powershell
$src = (Get-Location).Path
$dst = Join-Path $HOME '.agents\skills\engineering-controller'

if (Test-Path $dst) {
    throw "Skill já existe em $dst. Atualize deliberadamente em vez de sobrescrever sem revisão."
}

New-Item -ItemType Directory -Path $dst -Force | Out-Null

Copy-Item "$src\SKILL.md" "$dst\SKILL.md"

foreach ($dir in @('agents','scripts','schemas','references','docs')) {
    Copy-Item "$src\$dir" "$dst\$dir" -Recurse
}
```

Estrutura esperada:

```text
~/.agents/skills/engineering-controller/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── controller.py
├── schemas/
├── references/
└── docs/
```

A Skill é **explicit-only**. O arquivo `agents/openai.yaml` define:

```yaml
policy:
  allow_implicit_invocation: false
```

Ou seja: uma tarefa comum no Codex não deve virar um Engineering Loop silenciosamente.

### 3. Preparar a tarefa

Entre no repositório que será o **PROJECT TARGET** e crie um prompt/SPEC dentro dele, por exemplo:

```text
TASK.md
```

Exemplo mínimo:

```markdown
# Tarefa

Implemente a alteração descrita abaixo.

## Objetivo

Criar `RESULT.md` com o conteúdo esperado e validar o resultado.

## Restrições

- não fazer commit;
- não fazer push;
- não mudar de branch;
- não executar operações destrutivas.
```

Antes de executar, mantenha o repositório em um estado previsível:

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
```

Por segurança, a v0.1.0 bloqueia situações como working tree inesperadamente suja, detached HEAD ou prompt fora do repositório alvo.

### 4. Executar

Abra o Codex **a partir da raiz do projeto alvo**:

```powershell
codex
```

Dentro do Codex, invoque explicitamente:

```text
$engineering-controller execute TASK.md
```

A partir daí, não implemente a tarefa manualmente em paralelo. O Controller passa a ser o dono do loop.

Fluxo normal:

```text
$engineering-controller execute TASK.md
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

O Worker e o Reviewer não são dois papéis dentro da mesma conversa. Eles são execuções `codex exec` separadas e recebem contextos diferentes.

### 5. Quando aparecer `HUMAN_REQUIRED`

O Controller interrompe o loop quando encontra uma decisão que não deve assumir sozinho.

Exemplos de flags globais:

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

Depois da decisão humana, retome explicitamente:

```text
$engineering-controller resume
```

ou, se houver uma resolução que precisa ser transmitida ao Worker:

```text
$engineering-controller resume <resolução humana>
```

O Controller não inventa a aprovação humana e não transforma `HUMAN_REQUIRED` em aprovação automática.

### 6. Estados e exit codes

```text
0   COMPLETED
1   FAILED
2   HUMAN_REQUIRED
130 INTERRUPTED
```

Estados internos principais:

```text
PREFLIGHT
WORKER
REVIEW
HUMAN_REQUIRED
FINAL_VALIDATION
COMPLETED
FAILED
```

### 7. Onde o estado fica salvo

Por padrão, o Controller mantém o runtime fora do projeto alvo:

```text
~/.engineering-controller/runs/<repo-hash>/<run-id>/
```

Arquivos típicos:

```text
current.json
state.json
worker-result.json
reviewer-result.json
run.log
```

Isso evita misturar estado operacional da Skill com o código do projeto que está sendo alterado.

Quando a Skill é executada a partir de uma sessão Codex sandboxed, o Codex externo pode pedir autorização pontual para criar ou escrever em `~/.engineering-controller`. Isso é esperado: aprove apenas o acesso específico necessário ao diretório de estado. Não use `yolo`, `danger-full-access` ou bypass de sandbox para contornar essa autorização.

## O que acontece por baixo dos panos

### Worker

O Worker recebe `workspace-write`, implementa a SPEC, executa validações e retorna um resultado estruturado. Os status mínimos são:

```text
COMPLETED
GATE_REQUIRED
FAILED
```

Quando um gate precisa de revisão, a mesma sessão Worker é preservada para `resume`.

### Reviewer

O Reviewer é iniciado como um processo separado, com contexto mínimo e sandbox `read-only`.

Ele recebe apenas o necessário para decidir:

```text
APPROVE
REVISE
HUMAN_REQUIRED
```

O Reviewer não edita arquivos, não faz commit, não faz push e não substitui os Hard Guards determinísticos do Controller.

### Controller

`scripts/controller.py` é a máquina de estados determinística. Entre outras coisas, ele controla:

- preflight Git;
- protocolos JSON;
- schemas e validação semântica;
- Worker e Reviewer;
- retomada da sessão Worker;
- limites de iteração;
- estado persistente;
- invariantes de branch/HEAD;
- proteção contra alterações inesperadas no `config.toml` do Codex;
- redaction de logs;
- `HUMAN_REQUIRED` global.

## Windows: setup e troubleshooting validado

A v0.1.0 foi validada no Windows com `codex-cli 0.147.0`. Durante a validação foram encontrados dois problemas de ambiente que podem bloquear uma Skill que lança subprocessos Codex.

As correções abaixo são **troubleshooting**, não passos obrigatórios para todo ambiente. Só aplique quando o erro correspondente existir.

### Problema 1 — helpers do sandbox Windows não encontrados

Sintoma observado:

```text
windows sandbox: orchestrator_helper_launch_failed
setup refresh failed to launch helper
helper=codex-windows-sandbox-setup.exe
error=program not found
```

No ambiente validado, o `codex.exe` ativo estava em:

```text
%LOCALAPPDATA%\Programs\OpenAI\Codex\bin
```

mas os helpers da mesma versão estavam apenas em:

```text
%USERPROFILE%\.codex\packages\standalone\releases\<VERSÃO>-x86_64-pc-windows-msvc\codex-resources
```

Primeiro descubra a versão e os caminhos reais. Não copie helpers de versões diferentes.

Exemplo PowerShell:

```powershell
codex --version

$version = '0.147.0' # substitua pela versão EXATA exibida pelo seu Codex
$src = "$env:USERPROFILE\.codex\packages\standalone\releases\$version-x86_64-pc-windows-msvc\codex-resources"
$dst = "$env:LOCALAPPDATA\Programs\OpenAI\Codex\bin"

$setup  = Join-Path $src 'codex-windows-sandbox-setup.exe'
$runner = Join-Path $src 'codex-command-runner.exe'

Get-FileHash $setup  -Algorithm SHA256
Get-FileHash $runner -Algorithm SHA256

Test-Path (Join-Path $dst 'codex-windows-sandbox-setup.exe')
Test-Path (Join-Path $dst 'codex-command-runner.exe')
```

Se os helpers da **mesma versão** existirem em `codex-resources` e estiverem ausentes do `bin` ativo, o workaround usado na validação foi disponibilizá-los ao lado do `codex.exe`:

```powershell
Copy-Item -LiteralPath $setup  -Destination $dst -ErrorAction Stop
Copy-Item -LiteralPath $runner -Destination $dst -ErrorAction Stop
```

Depois confirme:

```powershell
Get-ChildItem -LiteralPath $dst |
    Where-Object Name -in @(
        'codex.exe',
        'codex-code-mode-host.exe',
        'codex-windows-sandbox-setup.exe',
        'codex-command-runner.exe'
    ) |
    Select-Object Name, Length, LastWriteTime
```

O Controller continua fail-closed. Ele não copia esses executáveis, não instala componentes e não faz fallback automático para um sandbox mais fraco.

### Problema 2 — Python da Microsoft Store / WindowsApps não executa no sandbox

Sintoma observado:

```text
rtk: Não é possível o acesso ao arquivo pelo sistema. (os error 1920)
```

ou falha equivalente ao tentar executar `python` de dentro do Codex.

Diagnóstico:

```powershell
where.exe python
python -c "import sys; print(sys.executable)"
```

No ambiente que falhou, `python` resolvia primeiro para:

```text
%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe
```

O PowerShell normal conseguia executar esse alias, mas o sandbox do Codex não.

Com o Python Install Manager, um alias normal pode existir em:

```text
%LOCALAPPDATA%\Python\bin\python.exe
```

Valide diretamente:

```powershell
$pythonBin = "$env:LOCALAPPDATA\Python\bin"

& "$pythonBin\python.exe" --version
& "$pythonBin\python.exe" -c "import sys; print(sys.executable)"
```

Para testar sem alterar permanentemente o Windows, coloque esse diretório no início do `PATH` da sessão **antes de abrir o Codex**:

```powershell
$env:PATH = "$env:LOCALAPPDATA\Python\bin;$env:PATH"

where.exe python
python --version
python -c "import sys; print(sys.executable)"

codex
```

O Codex iniciado nessa mesma janela herda o `PATH` corrigido.

No smoke test validado, esse ajuste permitiu que a Skill executasse:

```text
[EC] Preflight OK
[EC] Worker #1 iniciado
[EC] Final validation OK
[EC] COMPLETED
```

### Sandbox Windows explícito

A v0.1.0 não depende de `config.toml` para escolher o backend do sandbox Windows. O Controller passa explicitamente, em Windows:

```text
--config windows.sandbox="elevated"
```

Isso é feito para Worker, Reviewer e Worker `resume`.

Ao mesmo tempo, cada `codex exec` continua usando:

```text
--ignore-user-config
--ask-for-approval never
```

O Worker recebe `workspace-write` e o Reviewer recebe `read-only`.

Não existe fallback automático para:

```text
windows.sandbox="unelevated"
```

Se o sandbox elevado não estiver disponível, a execução deve falhar fechada como problema de ambiente.

## Agnóstico por projeto

O `engineering-controller` é intencionalmente **agnóstico de domínio e stack**. Ele não contém conhecimento específico de banco de dados, framework, linguagem, fornecedor, produto ou aplicação.

A Skill trata o repositório em que foi invocada como **PROJECT TARGET** e recebe o domínio exclusivamente do prompt/SPEC do próprio projeto.

Isso significa que o mesmo Controller pode operar, por exemplo, em projetos Python, Node.js, .NET, Java, infraestrutura, documentação ou automação — desde que o alvo seja um repositório Git e cumpra os pré-requisitos operacionais da Skill.

Políticas específicas de um projeto podem **restringir** o comportamento do Controller, mas nunca enfraquecer suas proteções globais.

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

## Escopo da v0.1.0

A v0.1.0 é uma **user-level Codex Skill** com:

- `SKILL.md` compacto;
- invocação explícita;
- `controller.py` em Python standard library;
- Worker e Reviewer executados por processos `codex exec` separados;
- JSON Schemas para os protocolos;
- sandbox `workspace-write` para Worker e `read-only` para Reviewer;
- estado persistente fora do repositório alvo;
- `resume` para ciclos interrompidos por `HUMAN_REQUIRED`;
- Git safety;
- Context Budget;
- RTK opcional;
- testes unitários, sintéticos e de integração.

## O que a v0.1.0 não é

Não é um framework genérico de agentes, servidor, daemon, API, dashboard, MCP, banco de dados, pipeline distribuído ou ferramenta de CI/CD.

Também não faz automaticamente merge, push, force push, reset destrutivo, exclusão de branch ou bypass de sandbox.

## Estrutura

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
├── docs/
│   └── SPEC-v0.1.md
└── tests/
```

## Segurança

O Controller falha fechado quando o protocolo, o estado ou uma operação de segurança não podem ser validados.

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
