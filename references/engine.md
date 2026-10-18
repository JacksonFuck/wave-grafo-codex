# Motor, reservas e evidências

Use `G` como abreviação de `python3 <skill>/scripts/grafo.py`; nos exemplos abaixo substitua por esse comando. `GRAPH` deve ser absoluto e único. O motor usa `flock` e troca atômica do JSON; não coordena duas cópias distintas do grafo.

## Inicializar ou retomar

```bash
G --path "$GRAPH" init --codex --test-cmd 'python3 -m unittest discover -s tests' --tracker local --base-branch main --max-parallel 2
G --path "$GRAPH" summary
G --path "$GRAPH" frontier
G --path "$GRAPH" show 12
```

Não usar `init --force` para retomar. Para um grafo legado, faça backup e use `codex-enable`: preserva estados/histórico, mas transições posteriores exigem as novas evidências. Não produz recibos retroativos. Confirme a base e o comando real do projeto; o exemplo de unittest não é um padrão universal. `TEST_CMD` é argv interpretado por `shlex`, sem shell: para pipelines, aponte para um script versionado.

```bash
G --path "$GRAPH" add --id 12 --title 'Corrigir cálculo' --files 'src/calculo.py,tests/test_calculo.py'
G --path "$GRAPH" claim 12 --owner wave-12
G --path "$GRAPH" set 12 plan=/abs/plans/12.md role=planner model=gpt-6.1-sol thinking=max activity=planning
G --path "$GRAPH" move 12 PLANEJADA
G --path "$GRAPH" move 12 PLANO_APROVADO --note 'Plano revisado por Sol max; relatório /abs/reviews/12-plan.json'
G --path "$GRAPH" set 12 worktree=/abs/worktrees/12 branch=wave/12 role=coder model=gpt-6-luna thinking=max activity=implementing
G --path "$GRAPH" move 12 EM_IMPLEMENTACAO --test-exit 0
```

Confirme a sintaxe de `add`/`move` com `--help` na versão instalada. A linha de base acima é uma declaração do Coordinator após executar o teste; não é recibo automático. Modelo/papel no grafo são metadados declarados: a prova da invocação está no runtime. O motor não dispara agentes nem publica PRs.

`frontier` só consulta. `claim` reserva sob lock, revalida dependências, arquivos e vagas; se falhar, recalcule. `release 12 --owner wave-12` só após o worker parar. Owner é controle cooperativo, não autenticação. Não liberar uma tarefa ativa para contornar vagas/colisões. `set` não substitui transições, e arquivos/dependências de uma reserva exigem liberação antes de alteração. Não retirar fatia que ainda tem worker ativo.

## Estados

Fluxo normal: `PRONTA → PLANEJADA → PLANO_APROVADO → EM_IMPLEMENTACAO → EM_AVALIACAO → APROVADA → PR_ABERTO → MERGEADA`. Revisão recusada segue `EM_CORRECAO → EM_AVALIACAO`. Conflito de base é `CONFLITO`, com nova rodada; `BLOQUEADA` registra motivo explícito. Consulte `move --help` e os erros de transição para os caminhos legais. Não usar estado ou nota como atalho para pular revisão.

## Executar RED e GREEN reais

O executor deve rodar na raiz do worktree da fatia, dentro de um repositório Git com HEAD e base válidos. `TEST_CMD` e gates são relativos a essa raiz; não apontar `--cwd` para um subdiretório com outra suíte. RED pode ser focal; GREEN deve corresponder exatamente ao `TEST_CMD` completo. Seu último resultado no ciclo prevalece: sucesso antigo não esconde falha posterior. A revisão humana/do modelo verifica que o RED falhou pelo motivo pretendido: qualquer saída não zero sozinha não prova TDD correto.

```bash
python3 <skill>/scripts/evidence.py --graph "$GRAPH" --id 12 --phase red --cwd /abs/worktrees/12 -- python3 -m unittest tests.test_calculo.TestTotal.test_limite
# Luna implementa, testa e faz o commit local antes do GREEN final.
python3 <skill>/scripts/evidence.py --graph "$GRAPH" --id 12 --phase green --cwd /abs/worktrees/12 -- python3 -m unittest discover -s tests
G --path "$GRAPH" move 12 EM_AVALIACAO
G --path "$GRAPH" move 12 APROVADA --score 90
```

O executor registra argv, saída real, hash do log, grafo/fatia/ciclo, HEAD, base e fingerprint antes/depois. Comandos que mudam o snapshot não geram evidência estável. A revisão deve cobrir todos os achados materiais, além da nota; o motor não interpreta relatórios de revisão nem valida sua autoria. Commits, alterações rastreadas, arquivos novos do escopo e mudança da base invalidam recibos correspondentes. Não mover a base nem alterar `TEST_CMD` para reutilizar resultados antigos.

A aprovação fica vinculada ao snapshot revisado. Se HEAD/código/base mudarem depois de `APROVADA`, volte à correção e revisão; um novo GREEN não substitui essa revisão. A base dos recibos deve ser a base configurada no grafo.

Para lint/tipos/build adicionais, executar `evidence.py --phase gate -- ...` por comando, corrigir falhas e repetir os afetados. O último recibo de cada comando de gate precisa estar verde, estável e vigente; outro comando que passou não apaga a falha. Um GREEN completo ainda vigente pode cumprir o gate de testes sem repetição; nenhum teste substitui outro comando exigido pelo projeto. Só registrar `PR_ABERTO --pr N --test-exit 0` após abertura real e autorizada; só `MERGEADA` após confirmação remota.

Os testes são serializados por `git common-dir`, inclusive entre worktrees do mesmo repositório. Essa trava cobre somente comandos executados por `evidence.py`; combinar com outros runners exige coordenação do Coordinator.

## Persistência e limites

Recibos/logs ficam em `/tmp/wave-grafo-evidence/<hash-do-grafo>` e locks em `/tmp/wave-grafo-repo-locks`. Podem desaparecer após limpeza/reboot: exporte os relatórios necessários ao checkpoint privado, e reexecute evidências se ausentes. Copiar só o JSON ou mover `GRAPH` não transporta sua cadeia de evidência.

HMAC local e checksums detectam adulteração acidental; processos do mesmo usuário têm acesso à chave e não são uma fronteira de segurança adversarial. Não publicar esses diretórios, logs ou chave. A garantia é para agentes cooperativos: o proprietário ainda pode editar o JSON diretamente, então todos os escritores devem usar o motor.
