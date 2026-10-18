# Kanban somente leitura

O painel mantém o funcionamento do original (colunas, cartões, refresh e versão textual), mas recebe a fonte por parâmetro e não depende de nomes do projeto ou symlinks. HTML e Markdown compartilham a mesma classificação em Python. `agent_id` é identidade, não fase; use `activity` explícita.

```bash
python3 "$SKILL/scripts/kanban.py" \
  --graph "$GRAPH" --output "$ARTIFACTS/kanban.md"

python3 "$SKILL/scripts/painel.py" --graph "$GRAPH" --port 8766
```

`GRAPH` e arquivo de saída são caminhos absolutos. O servidor liga somente em `127.0.0.1`; escolher porta livre, sem parar outro painel. Abrir `http://127.0.0.1:8766/`. O servidor é auxiliar local, não hospedagem/painel público.

Rotas: `/` para HTML, `/board.json` para a visão derivada e `/kanban.md` para texto atualizado. Não serve diretórios, arquivos de prompts/recibos, credenciais nem uma cópia mutável do grafo. POST/PUT/PATCH/DELETE são recusados. Parar o processo com Ctrl+C quando não precisar do painel.

Cartões mostram estado/atividade, papel, agente, modelo, thinking, dependências, nota, PR e último evento/hora UTC. `activity`: `research`, `planning`, `plan_ready` quando aplicável a PRONTA; os demais estados seguem a máquina de estados. Um card aparece em exatamente uma coluna, inclusive dados legados/estado desconhecido. MERGEADA permanece em Feito; "hoje" é filtro explícito UTC, não exclusão silenciosa do histórico. Título legado PAUSADA fica visível em bloqueados.

Links de PR só são criados para número positivo e URL HTTPS de repo GitHub configurada em `config.tracker_url` ou `config.tracker`. Sem URL válida, apenas mostrar o número. Conteúdo do grafo é texto, sem execução de HTML/scripts. Erro de leitura do grafo deve ficar visível como erro, não como falsa atualização bem-sucedida.

O refresh automático não chama modelos. O Coordinator usa `summary`/`show` em vez de screenshots ou HTML completo para tomar decisões. O usuário pode acompanhar o painel sem consumir contexto de agentes.
