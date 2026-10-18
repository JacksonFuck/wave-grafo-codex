# Runtime Codex e modelos

## Nativo, quando disponível

Preferir as ferramentas de colaboração já expostas na sessão: não instalar outro orquestrador nem chamar uma API paga externa. Conferir os parâmetros do runtime antes de usar.

Nesta interface, uma delegação isolada tem a forma:

```text
collaboration.spawn_agent(
  task_name="planejar_fatia_42", fork_turns="none",
  model="gpt-6.1-sol", reasoning_effort="max",
  message="Objetivo, repo, GRAPH, plano/artefatos, regras e resultado esperado"
)
```

Para `coder`/`corrector`, mudar somente o modelo para `gpt-6-luna`, mantendo `max`. Planner/revisor devolvem plano/JSON, sem editar o código. Não usar o mesmo contexto para implementar e aprovar. Em runtimes que usam outros nomes de ferramentas, aplicar o contrato equivalente disponível, sem inventar `Agent`, `SendMessage`, `Plan` ou `/loop` do Claude.

Use mensagem para agente ativo; tarefa de continuação para agente ocioso quando a API diferenciar ambos. Reusar o worker para correções dentro do mesmo escopo; reviewer sempre independente. Após reinício, IDs antigos não indicam agente vivo: consultar o runtime e reaproveitar o worktree com contexto curto.

A skill não muda o modelo raiz por texto. Se a sessão não é Sol 6.1 max, delegar o papel Coordinator a uma sessão Sol max ou usar o executor abaixo, entregando objetivo e permissões vigentes. Não iniciar outro Coordinator para o mesmo grafo enquanto o primeiro estiver ativo. Arquivos de agentes personalizados podem sobrepor o modelo solicitado: verificar a configuração efetiva e registrar o modelo realmente invocado.

## Executor CLI alternativo

Requer Codex CLI já instalado e autenticado por um método permitido no ambiente. Usa a autenticação existente; não pede API key, troca provider ou modifica config global. A disponibilidade na conta precisa incluir os modelos pedidos. Falha/quota não causa fallback ou retry infinito.

```bash
python3 "$SKILL/scripts/run_role.py" \
  --role coder --repo "$WORKTREE" \
  --prompt-file /tmp/wave-42/coder.txt \
  --output /tmp/wave-42/coder-result.txt --dry-run
```

Retirar `--dry-run` executa uma chamada real ao Codex. `--dry-run` só exibe argv/papel/modelo/effort/sandbox e não inicia modelo. Diretórios e arquivos devem ser absolutos. Usar nomes de saída novos por tentativa, pois arquivos existentes não são sobrescritos.

Papéis: `coordinator`, `planner`, `plan-reviewer`, `reviewer`, `coder`, `corrector`. O mapa fixo está em `agents/models.json`; os seis usam `max`. Sandbox solicitado: read-only para planejamento/revisões, workspace-write para Coordinator/código, sempre sujeito às permissões externas e às regras herdadas. O executor não usa flags de bypass de aprovação/sandbox nem ignora a configuração do usuário.

O prompt vai por stdin, sem interpolação de shell. A última resposta vai para `--output`; eventos completos para o arquivo irmão `.events.jsonl`; o Coordinator recebe só resumo e caminhos. Eventos podem conter código/dados privados: guardar fora do git e não servir pelo painel. Não confundir exit0 do processo com verificação da feature; tests/recibos/revisão continuam obrigatórios.

`--check-models /caminho/models_cache.json` verifica modelo e esforço no cache local, sem chamar o modelo; esse cache não prova disponibilidade atual da conta. Ausência no cache impede essa checagem, mas não justifica escolher outro modelo. Sem métrica disponível, custo/tokens ficam desconhecidos; preços de API não equivalem à cota do plano ChatGPT.

## Continuidade

Cada tick encerra com checkpoint. Continuar ticks na sessão quando o usuário pedir execução contínua; não presumir um scheduler ou daemon criado por instalar a skill. Ao esperar somente aprovação, quota ou evento externo, devolver o checkpoint em vez de fazer polling caro com LLM. Nenhum merge/deploy ocorre por simples passagem de tempo.

## Fontes verificadas em 18/10/2026

- [Subagents — documentação oficial](https://learn.chatgpt.com/docs/agent-configuration/subagents): modelos/esforço explícitos, herança e agentes personalizados.
- [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) e [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).
- Interface local `codex exec --help` em Codex CLI 0.162.1 e catálogo da sessão: ambos os IDs e `max` confirmados. Revalidar flags/capacidades quando atualizar o cliente; não fixar uma versão de instalação global por esta skill.
