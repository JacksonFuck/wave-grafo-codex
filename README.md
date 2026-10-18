# wave-grafo-codex

Skill para executar um backlog como grafo de dependências, com worktrees isoladas, planos revisados, testes verificáveis e Kanban local. Adaptada do workflow `wave-grafo` e de seu painel de acompanhamento para o Codex.

## Papéis fixos

| Trabalho | Modelo | Thinking |
|---|---|---|
| Orquestração, planejamento, revisão do plano e do código | GPT-6.1 Sol (`gpt-6.1-sol`) | `max` |
| Implementação e correções | GPT-6 Luna (`gpt-6-luna`) | `max` |

Sem substituição automática de modelo. Se o Luna estagna, o Sol reavalia o plano; o Luna continua escrevendo o código. Conta/runtime precisam ter acesso aos dois modelos. A skill não muda por texto o modelo da sessão atual: há delegação nativa e um executor CLI com modelo explícito.

A economia é de contexto: resumos curtos, artefatos por fatia, grafo e painel determinísticos, leitura seletiva e reuso de verificações ainda válidas. Não há promessa de porcentagem de economia nem redução do thinking escolhido.

## Instalar

Python 3.10+ em Linux/macOS/WSL, Git e Codex autenticado. O motor usa `fcntl`; Windows nativo não é suportado. O Kanban não tem dependências Python externas. GitHub CLI só é necessário quando o tracker/publicação usa GitHub.

```bash
git clone https://github.com/JacksonFuck/wave-grafo-codex.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/wave-grafo-codex"
```

Não sobrescrever uma instalação existente com trabalho local. A pasta contém `SKILL.md`; abrir uma nova sessão/recarregar a descoberta de skills se ela ainda não aparecer no cliente.

## Usar

No Codex:

```text
$wave-grafo-codex execute as fatias prontas deste projeto com no máximo 2 implementadores.
```

Informe ou confirme a fonte do backlog e o grafo existente. Para projeto novo, a skill usa `<repo>/tasks/graph.json`; para retomar um projeto, usa exatamente o grafo escolhido. Não importa automaticamente dados de outro projeto.

O fluxo é: reservar fatia → Sol planeja → outro Sol revisa → Luna implementa/testa → Sol revisa → gate → PR/merge quando autorizados. O Coordinator é o único escritor do grafo. A skill não concede autorização automática para push, PR, merge, deploy ou mudanças de credenciais.

- [Instruções completas](SKILL.md)
- [Runtime e delegação](references/runtime.md)
- [Motor e evidências](references/engine.md)
- [Rubrica da revisão](references/review.md)

## Kanban

```bash
SKILL="${CODEX_HOME:-$HOME/.codex}/skills/wave-grafo-codex"
GRAPH="/caminho/absoluto/do/projeto/tasks/graph.json"

python3 "$SKILL/scripts/painel.py" --graph "$GRAPH" --port 8766
```

Abrir `http://127.0.0.1:8766/`. O painel é somente leitura, atualiza a cada 15 segundos e mostra estado, agente, modelo, thinking, dependências e PR. O mesmo grafo alimenta HTML e Markdown; nenhuma cópia editável ou chamada de LLM é criada pelo refresh.

```bash
python3 "$SKILL/scripts/kanban.py" \
  --graph "$GRAPH" --output /caminho/absoluto/kanban.md
```

[Detalhes do painel](references/kanban.md). Não expor o servidor na internet.

## Conferir modelos sem consumir inferência

```bash
python3 "$SKILL/scripts/run_role.py" \
  --role coder --repo /caminho/absoluto/do/worktree \
  --prompt-file /caminho/absoluto/prompt.txt \
  --output /tmp/wave-coder-result.txt --dry-run
```

O prompt precisa existir; a saída deve ser um caminho novo. O comando mostra `gpt-6-luna`, `max` e os argumentos sem executar um modelo. Sem `--dry-run`, a chamada ao Codex é real e usa sua conta. Papéis/efforts estão em [agents/models.json](agents/models.json).

## Testar

```bash
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```

Os testes usam repositórios temporários e um Codex falso; não fazem inferência paga nem modificam repositórios de trabalho. Os testes HTTP precisam de permissão de bind em loopback. Em sandbox que a negue, esses testes informam skip; isso não equivale a cobertura HTTP aprovada.

## Limites e origem

Mantém o DAG, estados e retomada do `wave-grafo`; a adaptação troca ferramentas Claude por capacidades do Codex, fixa os modelos solicitados e torna o Kanban portátil. O material distribuído é genérico: não inclui grafos de produção, logs reais, tokens, configurações de conta ou caminhos privados do projeto de origem.

Recibos locais detectam evidência obsoleta/adulteração acidental e não substituem revisão humana/modelo. Um agente com os mesmos privilégios de filesystem não é isolado criptograficamente só por haver checksum/assinatura. Sandbox e permissões do ambiente continuam sendo os limites efetivos.
