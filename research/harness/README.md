# harness/ — instrumentação do experimento de roteamento

Código versionado (`.py` + o `GOLDEN_ROUTING.example.json`). As saídas são regeneráveis e ficam no `.gitignore`.

## Módulos

| arquivo | papel |
|---|---|
| `conditions.py` | materializa C0/C1/C2 (`make_catalog`, `route`) sem tocar produção |
| `ontology_boost.py` | re-ranking ontológico de C2 (fenômeno, grão direcional, perspectiva) + `explicar` para error analysis |
| `metrics.py` | top-k, MRR, McNemar exato (sem scipy), pares de confusão |
| `run_benchmark.py` | runner das 3 condições + comparações pareadas; carrega `bindings_derivados.json` se presente |
| `gerar_bindings.py` | deriva `dataset_bindings` dos contratos (ancoragem no corpus) |
| `gerar_gold_candidatos.py` | gera candidatos a gold a partir dos contratos, com proveniência + categoria |
| `split_gold.py` | separa candidatos em DEV (contrato) e TESTE (sintético), com a trava de vazamento |
| `calibrar.py` | coordinate ascent dos pesos do boost no DEV; grava `pesos_boost.json` |
| `unit_conditions.py` / `run_unidade.py` | experimento end-to-end de unidade/aditividade (ver `../PROTOCOLO_END2END.md`) |

## Fluxo completo

```bash
# 1. bindings da ontologia (uma vez, ou após editar fenomenos/contratos)
python -m research.harness.gerar_bindings \
    --ontologia research/ontologia/ons_ontologia.json \
    > research/ontologia/bindings_derivados.json

# 2. candidatos a gold (depois: REVISAR O JSONL À MÃO)
python -m research.harness.gerar_gold_candidatos \
    --ontologia research/ontologia/ons_ontologia.json \
    --saida research/harness/gold_candidatos.jsonl

# 3. split dev/teste com as travas de validade
python -m research.harness.split_gold \
    --entrada research/harness/gold_candidatos.jsonl \
    --dev research/harness/GOLDEN_DEV.json \
    --teste research/harness/GOLDEN_TESTE.json

# 4. (opcional) calibrar os pesos do boost no DEV — nunca toca o teste
python -m research.harness.calibrar \
    --dev research/harness/GOLDEN_DEV.json \
    --ontologia research/ontologia/ons_ontologia.json \
    --saida research/harness/pesos_boost.json --metrica mrr

# 5. benchmark (use --saida; a saída JSON é mais confiável que o stdout no Windows).
#    Carrega pesos_boost.json automaticamente se existir; aplica só em C2.
python -m research.harness.run_benchmark \
    --gold research/harness/GOLDEN_TESTE.json \
    --ontologia research/ontologia/ons_ontologia.json \
    --saida research/harness/rodada.json
```

## Resultado da calibração (dev 308 → teste 126)

O otimizador **zerou** fenomeno_match/conflito e grao_exato, mantendo só penalidades de grão
e perspectiva. No teste congelado: C1 top-1 0.802 vs C2-calibrado 0.810, McNemar p=1.0
(b=0, c=1). Empate — a calibração não reverte o resultado, apenas o fecha com rigor.

## Nota sobre Windows / stdout

No PowerShell, capturar o stdout do runner por pipe (`| Out-File`, `| Tee`) pode travar ou
dar `UnicodeEncodeError` nos caracteres de caixa. **Use `--saida arquivo.json`** e leia o
JSON — é a fonte confiável das métricas, McNemar e confusão. O runner já força UTF-8 no
stdout, mas o arquivo de saída é o canal recomendado.

## Resultado conhecido (126 perguntas de teste, não calibrado)

C0 top-1 0.34 → C1 0.80 (p<1e-13) → C2 0.79 (empata C1, p=1.0). Ver `../PROTOCOLO.md §9.1`.
Antes de concluir sobre H2, calibre os pesos de `ontology_boost.py` no `GOLDEN_DEV.json`.
