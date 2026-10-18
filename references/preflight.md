# Pré-voo sob demanda

1. Conferir AGENTS.md, permissões, estado do git/worktrees e fonte real do backlog. Não limpar/resetar árvores alheias. Anotar sucesso verificável e hipóteses em `tasks/todo.md` ou local equivalente definido pelo projeto.
2. Conferir Python 3, git e toolchain do repo; `gh`/conector só se tracker/publicação precisar. Python do motor usa `fcntl` (Linux/macOS/WSL); Windows nativo requer adaptação, não fingir suporte. Não instalar ferramentas/configurar credenciais automaticamente.
3. Conferir runtime/modelos. Modelos pedidos: `gpt-6.1-sol` e `gpt-6-luna`, ambos `max`. O catálogo local/da sessão determina suporte; o cache pode estar desatualizado. Não fazer chamada paga de teste para cada tick.
4. Escolher `GRAPH` absoluto, raiz de artefatos e worktree root. Não reaproveitar o grafo de outro projeto. Para usar um legado, habilitar guardas pelo comando do [motor](engine.md); nunca sobrescrever via init/force.
5. Ler manifesto/lockfile/scripts antes de executar. Instalar pelo gerenciador do projeto com lockfile; seguir autorizações e sandbox. Mudança de manifesto, lockfile, toolchain, env de testes ou base obriga revalidar evidência afetada.
6. Determinar TEST_CMD e checks pertinentes, registrando ambiente e baseline. Baseline falha: diagnosticar; não chamar isso de regressão da fatia nem prosseguir como verde. Não rodar a suíte pesada em paralelo com outro worker/reviewer.
7. Reservar vagas para Sol revisor e Coordinator; `max_parallel` é workers, não licença para exceder threads reais. Começar com 2 workers, baixar se RAM/CPU não suportar. Não executar todos os pacotes paralelos de um monorepo quando o projeto pede sequência.

Sem novas dependências desta skill: motor, painel e executor usam biblioteca padrão Python; git/Codex CLI são ferramentas do ambiente. Não alterar `~/.codex/config.toml`, agentes globais, integrações, aprovação ou acesso à rede para "fazer funcionar". Se houver impedimento, apresentar o comando/resultado concreto e a alternativa que preserva o escopo.
