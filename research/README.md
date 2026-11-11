# research/ — Estudo: data contracts vs. data contracts + ontologia

Pacote de pesquisa **isolado do código de produção** (`src/mcp_tiago_dados_abertos`). Mede se uma ontologia de domínio melhora o roteamento NL→dataset do servidor MCP sobre a base já forte de data contracts ODCS.

Nada aqui é importado pelo servidor nem distribuído no wheel.

## Estrutura

```
research/
├── PROTOCOLO.md              # metodologia completa (vira seção de Métodos do paper)
├── ontologia/
│   ├── ons_setor_eletrico.ttl   # ontologia OWL/Turtle (formal, citável)
│   ├── ons_ontologia.json       # projeção operacional (o harness carrega esta)
│   └── README.md                # o que a ontologia modela e como ancorá-la no corpus
└── harness/
    ├── conditions.py            # condições C0/C1/C2 (ablação sem tocar produção)
    ├── ontology_boost.py        # re-ranking ontológico — o que distingue C2 de C1
    ├── metrics.py               # top-k, MRR, McNemar exato, pares de confusão
    ├── run_benchmark.py         # runner das 3 condições + comparações pareadas
    ├── gerar_bindings.py        # deriva os bindings da ontologia dos contratos
    └── GOLDEN_ROUTING.example.json  # 24 perguntas semente (expandir p/ 300-500)
```

## Começo rápido

```bash
# roteamento é offline (não lê o S3); basta o pacote no path
python -m research.harness.run_benchmark \
    --gold research/harness/GOLDEN_ROUTING.example.json \
    --ontologia research/ontologia/ons_ontologia.json
```

Saída esperada (piloto de 24 perguntas): C0 top-1 ≈ 0.63, C1 ≈ 0.79, C2 ≈ 0.79. Ver `PROTOCOLO.md §9` para a leitura.

## Mapa conceitual

- **C0 → C1** mede o valor dos data contracts (reconfirma a literatura).
- **C1 → C2** é a pergunta de pesquisa: o que a ontologia adiciona por cima.
- O ganho esperado de C2 é **concentrado** nos pares de confusão, grão espacial e perspectiva temporal — não no agregado. Daí a estratificação do gold set.

Leia o `PROTOCOLO.md` antes de rodar o estudo real: tamanho de amostra, anotação com κ, separação dev/teste, calibração dos pesos e ameaças à validade estão todos lá.
