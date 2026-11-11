# Ontologia mínima do domínio ONS

Camada **C2** do experimento (data contracts + ontologia). Dois arquivos, mesma ontologia:

| arquivo | formato | para quê |
|---|---|---|
| `ons_setor_eletrico.ttl` | Turtle / OWL leve | representação formal, citável no paper, inspecionável com Protégé/rdflib |
| `ons_ontologia.json` | JSON operacional | o que o harness de roteamento carrega em tempo de execução |

Mantenha os dois em sincronia manual. O `.ttl` é a fonte conceitual; o `.json` é a projeção que o código consome.

## O que a ontologia modela (e os contratos não)

Os contratos ODCS são ricos **por dataset**, mas não têm vínculo entre si. A ontologia adiciona exatamente três eixos de relação que atravessam datasets:

1. **Hierarquia espacial** (`parteDe`, transitiva): Usina ⊂ Reservatório ⊂ REE ⊂ Subsistema ⊂ SIN; Bacia ⊂ Subsistema. É o que habilita perguntas de composição ("usinas da bacia X no subsistema Y").
2. **Taxonomia de grandezas e aditividade**: PotênciaMédia (MWmed, `average`), Energia (MWh, `additive`), EstoqueEnergia (MWmês, `stock`), Vazão, Preço. O campo `aditividade_temporal` é o que previne somar estoque ou média ao longo do tempo.
3. **Perspectiva temporal como classe**: Verificado / Programado / Previsto / Cadastral — deixa o roteador raciocinar sobre o eixo em vez de casar palavra.

A ponte com os datasets é `Dataset --mede--> Fenômeno`, `--temGrao--> EntidadeEspacial`, `--temPerspectiva--> PerspectivaTemporal` (bloco `dataset_bindings` no JSON).

## Como gerar/validar `dataset_bindings`

O JSON traz uma semente curada. O mapa completo (78 datasets) é **derivável dos próprios contratos**, sem anotação manual, a partir do bloco `semantics`:

- `semantics.measure_terms` → `mede` (fenômeno)
- `semantics.row_grain.entity` → `grao` (entidade espacial)
- `semantics.temporal_perspective` → `perspectiva`

Rode `python -m research.harness.gerar_bindings --ontologia research/ontologia/ons_ontologia.json > research/ontologia/bindings_derivados.json` para emitir o mapa a partir de `contracts/ons/*.odcs.yaml`. Adicione `--comparar` para ver, em stderr, a cobertura e as divergências vs a semente curada. Isso mantém a ontologia **ancorada no corpus**, não em intuição — importante para a validade do experimento: C2 não introduz conhecimento externo que C1 não poderia ter.

**Cobertura atual** (77 datasets ativos): 74 com fenômeno (96%), 50 com grão espacial (os demais declaram `entity=dataset_row`, sem grão), 77 com perspectiva (100%). O arquivo gerado `bindings_derivados.json` é carregado automaticamente pelo runner quando presente nesta pasta; a semente embutida em `ons_ontologia.json` (12 datasets) é só fallback. Regenere o arquivo sempre que editar os `fenomenos`/`aliases` ou os contratos.

## Nota de validade experimental

A ontologia é deliberadamente mínima e derivada do corpus. Isso é intencional: se C2 vencer C1, o ganho vem de **estruturar e relacionar** conhecimento que já estava nos contratos de forma dispersa, não de injetar fatos novos. Essa é a hipótese científica honesta — "ontologia como organização de conhecimento existente" — e evita o viés de dar a C2 informação que C1 nunca teve.
