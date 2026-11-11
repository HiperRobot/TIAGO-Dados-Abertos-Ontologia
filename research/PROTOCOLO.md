# Protocolo experimental — Data contracts vs. data contracts + ontologia no roteamento NL→dataset

Documento de metodologia para o estudo que mede o efeito de uma **ontologia de domínio** sobre a performance do servidor MCP TIAGO Dados Abertos, que já opera com **data contracts** (ODCS). Escrito para virar a seção de Métodos de um artigo e para ser executável pelo harness em `research/harness/`.

---

## 1. Pergunta de pesquisa e hipóteses

A camada semântica (data contracts) já é estado da arte reconhecido: a literatura mostra que uma representação estruturada melhora a precisão de sistemas LLM sobre bancos de dados ([Sequeda et al., arXiv 2311.07509](https://arxiv.org/html/2311.07509); [arXiv 2405.11706](https://arxiv.org/html/2405.11706v1); [arXiv 2606.31041](https://arxiv.org/pdf/2606.31041)). *Conteúdo das fontes reformulado para conformidade de licenciamento.*

Este servidor **já tem** data contracts ricos. A questão não é "camada semântica ajuda?" (respondida), e sim:

> **RQ:** Uma ontologia de domínio formal, que estrutura e relaciona o conhecimento já presente nos data contracts, melhora o roteamento de perguntas para datasets além do que os contratos isolados alcançam?

**Hipóteses:**

- **H1 (reconfirma a literatura):** C1 (contracts) supera C0 (schema puro) em top-1 e MRR, com significância.
- **H2 (a tese):** C2 (contracts + ontologia) supera C1 em top-1 e/ou MRR.
- **H2a (hipótese refinada, mais provável):** o ganho de C2 sobre C1 **concentra-se** em subconjuntos específicos — pares de confusão (fenômenos vizinhos), perguntas com grão espacial ambíguo e desambiguação de perspectiva temporal — e é **nulo ou marginal** em perguntas simples de dataset único.

O desenho busca **falsear H2**, não confirmá-la. Se C2 ≈ C1, esse é um resultado publicável e honesto: data contracts bem construídos já capturam a maior parte do valor semântico.

---

## 2. Condições (arms) — estudo de ablação

| Cond. | Nome | O que contém | Operacionalização |
|-------|------|--------------|-------------------|
| **C0** | schema-only | nome do dataset + colunas (nome/tipo), sem curadoria | `conditions.make_catalog(cat, "C0")` zera tags, vectorTags, aiContext, typicalQuestions, semantics e descrições de coluna |
| **C1** | data contracts | ODCS completo (estado de produção) | catálogo de `load_all_contracts()` sem alteração |
| **C2** | contracts + ontologia | C1 + re-ranking ontológico | `ontology_boost.aplicar_boost()` sobre o ranking de C1 |
| **C3** *(opcional)* | ontologia-only | ontologia sem a riqueza textual dos contratos | C0 + boost ontológico; isola a contribuição pura da ontologia |

O par decisivo para a RQ é **C1 vs C2**. C0 é o piso; C3 serve para decompor onde mora o sinal.

**Garantia de validade:** a ontologia (`research/ontologia/`) é **derivada do próprio corpus** (`gerar_bindings.py` extrai os bindings de `semantics.measure_terms`, `row_grain.entity`, `temporal_perspective`). C2 não injeta conhecimento externo que C1 não poderia ter — apenas o **estrutura e relaciona**. Isso impede o viés de "dar a C2 uma informação a mais".

---

## 3. Materiais

- **Corpus:** 78 contratos ODCS em `contracts/ons/*.odcs.yaml` (1 órgão, ONS).
- **Pipeline de roteamento:** `catalogo/ranking.py` (`rank_datasets`) = BM25 (`retrieval.py`, k1=1.5, b=0.4) + keyword scoring. Ponto único de roteamento, 100% offline (não lê o S3).
- **Ontologia:** `research/ontologia/ons_setor_eletrico.ttl` (OWL/Turtle) e `ons_ontologia.json` (projeção operacional).
- **Harness:** `research/harness/` — `conditions.py`, `ontology_boost.py`, `metrics.py`, `run_benchmark.py`, `gerar_bindings.py`.

---

## 4. Gold set (conjunto de avaliação)

### 4.1 Roteamento (experimento principal)

Cada item: `{pergunta, esperado (name do dataset), categoria}`. Formato em `GOLDEN_ROUTING.example.json` (24 perguntas semente).

**Para o estudo real:**
- **Tamanho:** 300–500 perguntas. Com top-1 base ~0.79, detectar um ganho de 5 pontos a 80% de poder exige ~250–350 pares discordantes úteis; 300–500 perguntas é a faixa segura.
- **Estratificação por categoria**, com sobre-representação deliberada dos casos onde a ontologia *deveria* agir:
  - `simples_subsistema`, `serie_temporal`, `coloquial` — onde se espera C1 ≈ C2 (controle).
  - `confusao_fenomeno` (carga vs balanço vs geração), `confusao_grao` (EAR/ENA por subsistema vs reservatório vs bacia), `confusao_perspectiva` (verificado vs programado/previsto), `inventario_vs_serie`, `composicao` (perguntas que exigem a hierarquia parteDe: "usinas da bacia X no subsistema Y") — onde se espera o efeito.
- **Anotação:** 2+ anotadores independentes; medir **Cohen's κ**. Resolver divergências por consenso. Reportar κ no paper.
- **Separação treino/teste:** reserve um **dev set** (≈20%) só para calibrar os pesos do boost (§6). O teste nunca entra na calibração. Isso evita o vício que o próprio `ROUTING.md` descreve ("se piorou em perguntas que não são do seu dataset, você está roubando tráfego").
- **Proveniência das perguntas:** combine (a) perguntas reais de uso, (b) paráfrases coloquiais, (c) perguntas derivadas de `typicalQuestions`/`fewShotQueries` — mas **nunca** use no teste as mesmas perguntas que entram no documento BM25 do contrato (vazamento).

### 4.2 End-to-end (experimento secundário, opcional)

Cada item: `{pergunta, resultado_gold}`. Use **períodos antigos estáveis** (ex.: 2020), pois o ONS revisa períodos recentes (ver README). Requer execução real contra o S3.

---

## 5. Métricas

### 5.1 Roteamento (por condição) — `metrics.avaliar`

| Métrica | Definição |
|---------|-----------|
| **top-1** | % de perguntas em que o dataset correto é o 1º |
| **top-3 / top-5** | idem para os 3/5 primeiros |
| **MRR** | média de 1/posição do dataset correto |
| **fora do top-10** | % sem o correto entre os 10 primeiros |
| **matriz de confusão** | pares (esperado → devolvido) mais frequentes |

### 5.2 Comparação pareada entre condições — `metrics.mcnemar`

**Teste de McNemar exato** (binomial bicaudal) sobre acertos top-1 pareados. É o teste correto: as condições veem as mesmas perguntas, então interessa a discordância (b = só A acerta, c = só B acerta), não as marginais. Reportar `b`, `c`, p-valor e direção.

Para MRR (contínuo) e EX end-to-end, usar **bootstrap** (10 000 reamostragens) para intervalo de confiança da diferença de médias, e reportar o IC junto do ponto.

### 5.3 Métricas específicas deste MCP (o diferencial do estudo)

Vão além do que Spider/BIRD medem e exploram o design "só afirma o que prova":

- **Acurácia de unidade** — a unidade declarada (MWmed/MWh/MWmês) bate com a correta?
- **Acurácia de aditividade** — o sistema evitou somar o que é estoque/média ao longo do tempo? (A ontologia modela isso em `aditividade_temporal`.)
- **Taxa de abstenção correta** — quando o contrato não prova, o servidor marca `unit: null`/`unknown`. Medir abstenção-correta vs erro-confiante é uma métrica rica e pouco usada na literatura.

### 5.4 Métricas de custo (contexto honesto)

- tokens de contexto por condição (C0 < C2);
- latência de roteamento (deve ser desprezível — tudo offline).

---

## 6. Calibração dos pesos do boost (só no dev set)

Os pesos em `ontology_boost.py` (`W_FENOMENO_MATCH=8`, `W_FENOMENO_CONFLITO=-8`, `W_GRAO_*=±6`, `W_PERSPECTIVA_*=±4`) são a configuração inicial, na mesma ordem de grandeza dos sinais de C1 (perspectiva=6, família=9, medida=6 em `ranking.py`). **Calibre-os exclusivamente no dev set** por grid search ou coordenada-a-coordenada, otimizando MRR no dev. Congele antes de tocar o teste. Documente a configuração final.

---

## 7. Procedimento

```bash
# 1. (uma vez) derivar os bindings da ontologia a partir dos contratos.
#    --comparar imprime cobertura e divergências vs a semente curada em stderr.
python -m research.harness.gerar_bindings \
    --ontologia research/ontologia/ons_ontologia.json \
    > research/ontologia/bindings_derivados.json

# 2. rodar o benchmark nas três condições. O runner carrega automaticamente
#    bindings_derivados.json (cobertura completa) se existir ao lado da ontologia;
#    senão usa a semente embutida. --bindings aponta um arquivo específico.
python -m research.harness.run_benchmark \
    --gold research/harness/GOLDEN_ROUTING.json \
    --ontologia research/ontologia/ons_ontologia.json \
    --saida research/harness/rodada_final.json
```

**Determinismo:** o roteamento é determinístico (BM25 + regras + desempate alfabético), então não há seed/estocasticidade na etapa de roteamento — cada rodada é reproduzível. (A estocasticidade só entra se o experimento end-to-end usar um LLM para gerar SQL; aí sim, múltiplas seeds e temperatura 0, reportando média ± desvio.)

---

## 8. Análise

1. **H1:** McNemar C0 vs C1. Espera-se C1 ≫ C0, p < 0.05.
2. **H2:** McNemar C1 vs C2 no gold completo. Reportar mesmo se nulo.
3. **H2a:** repetir C1 vs C2 **por categoria**. É aqui que o efeito, se existir, aparece. Reportar top-1/MRR por estrato com IC bootstrap.
4. **Error analysis:** `metrics.top_pares_confusao` + `ontology_boost.explicar(pergunta)` para cada pergunta onde C2 mudou o resultado (para melhor e para pior). Classificar os ganhos e as regressões.
5. **Correção para múltiplas comparações:** ao testar várias categorias, aplicar Holm-Bonferroni sobre os p-valores por estrato.

---

## 8-bis. Fluxo de construção do gold set

O harness gera **candidatos** a partir dos contratos para dar partida na anotação, com as travas de validade embutidas:

```bash
# a) gerar candidatos rotulados (proveniência + categoria) a partir dos contratos
python -m research.harness.gerar_gold_candidatos \
    --ontologia research/ontologia/ons_ontologia.json \
    --saida research/harness/gold_candidatos.jsonl --por-dataset 4

# b) <<< REVISÃO HUMANA do JSONL: corrigir, remover ruins, reescrever paráfrases >>>

# c) separar dev/teste com as travas (teste só recebe fonte != contrato)
python -m research.harness.split_gold \
    --entrada research/harness/gold_candidatos.jsonl \
    --dev research/harness/GOLDEN_DEV.json \
    --teste research/harness/GOLDEN_TESTE.json
```

**Trava de vazamento:** perguntas `fonte="contrato"` estão no índice BM25 (`retrieval._documento` indexa `typicalQuestions` e `fewShotQueries[].question`). O `split_gold` as manda para o **dev** (calibração do boost) e nunca para o **teste**. O teste recebe só perguntas sintéticas/escritas à mão.

**Limitação das paráfrases automáticas:** as transformações de `gerar_gold_candidatos` são conservadoras (substituições de superfície) e ficam perto do original. Elas são *material de partida*, não gold final — a etapa (b) de reescrita humana é obrigatória para o teste ser honesto.

---

## 9. Resultados

### 9.1 Conjunto de teste limpo (126 perguntas sintéticas, sem vazamento)

Gerado pelo fluxo acima, estratificado (simples_subsistema 21, serie_temporal 26, ranking 15, inventario 10, confusao_grao 17, perspectiva_futura 7, outro 30). Bindings com cobertura completa (77 datasets).

| Cond. | top-1 | top-3 | top-5 | MRR | fora top-10 |
|-------|-------|-------|-------|-----|-------------|
| C0 | 0.341 | 0.548 | 0.683 | 0.470 | 0.286 |
| C1 | **0.802** | 1.000 | 1.000 | **0.896** | 0.000 |
| C2 | 0.794 | 0.992 | 1.000 | 0.889 | 0.000 |

McNemar (top-1):

| Comparação | b (só A) | c (só B) | p-valor | Direção |
|---|---|---|---|---|
| C0 vs C1 | 5 | 63 | 7.7×10⁻¹⁴ | **C1 ≫ C0** |
| C0 vs C2 | 4 | 61 | 3.9×10⁻¹⁴ | **C2 ≫ C0** |
| C1 vs C2 | 4 | 3 | 1.00 | C1 ≈ C2 |

**Conclusões:**
- **H1 confirmada com significância forte** (p < 10⁻¹³): os data contracts saltam o top-1 de 0.34 para 0.80 sobre schema puro.
- **H2 não confirmada; H2a sustentada:** a ontologia com pesos não-calibrados **empata** com C1 (p=1.0), corrigindo 3 perguntas e regredindo 4. Sobre contratos maduros, uma ontologia genérica não melhora o roteamento no agregado.
- **Onde o erro mora:** os pares de confusão residuais de C2 são majoritariamente **indicadores** (`ind_confiarb_*`, `ind_disponibilidade_*`, com `measure_terms` vazio no contrato) e a tríade REE↔reservatório↔subsistema. O gargalo é a completude do contrato na fonte, não a ontologia.

**Ressalva importante:** este teste de 126 perguntas (a) não teve os pesos do boost calibrados no dev set — a §6 exige essa calibração antes de uma conclusão definitiva sobre H2; e (b) as paráfrases sintéticas automáticas ainda carecem da reescrita humana da etapa 8-bis(b). O resultado é **indicativo**, mais forte que o piloto de §9.2 mas ainda não o estudo final.

### 9.1-bis Calibração dos pesos do boost (dev) e teste congelado

Os pesos de `ontology_boost.py` foram calibrados por *coordinate ascent* no `GOLDEN_DEV.json`
(308 perguntas, as mesmas perguntas-fonte do contrato — permitido no dev), otimizando MRR, via
`calibrar.py`. Os pesos ótimos foram **congelados** e aplicados ao `GOLDEN_TESTE.json`
(126 perguntas sintéticas, intocadas pela calibração).

**O que o otimizador escolheu:** `fenomeno_match=0`, `fenomeno_conflito=0`, `grao_exato=0` —
ou seja, **zerou os sinais centrais do boost**, mantendo só penalidades de grão
(`ancestral=-3`, `incompativel=-9`) e a perspectiva (`±4`). O calibrador descobriu sozinho
que o melhor uso da ontologia no roteamento é quase desligá-la.

No dev: C1 MRR 0.899 / top-1 0.812; C2 default MRR 0.886 (pior que C1); C2 calibrado MRR
0.902 / top-1 0.818 (ganho ~0.003 MRR — ruído).

No teste, pesos congelados:

| Cond. | top-1 | top-3 | MRR |
|-------|-------|-------|-----|
| C1 | 0.802 | 1.000 | 0.896 |
| C2 calibrado | 0.810 | 1.000 | 0.899 |

McNemar C1 vs C2-calibrado: **b=0, c=1, p=1.0**. A calibração corrigiu exatamente uma
pergunta no teste e não piorou nenhuma — estatisticamente **empate**.

**Conclusão reforçada:** mesmo com os pesos otimizados num dev set separado, a ontologia
não supera os data contracts no roteamento (p=1.0). Isso fecha a brecha "e se os pesos
estivessem mal escolhidos?": estavam perto do ótimo possível, e o ótimo possível é ≈ C1.
O resultado H2a — ganho nulo no agregado — é robusto à calibração.

### 9.2 Piloto inicial (24 perguntas — ilustrativo, NÃO conclusivo)

Rodado com `GOLDEN_ROUTING.example.json` para validar o pipeline:

| Cond. | top-1 | top-3 | top-5 | MRR | fora top-10 |
|-------|-------|-------|-------|-----|-------------|
| C0 | 0.625 | 0.750 | 0.875 | 0.712 | 0.042 |
| C1 | 0.792 | 0.958 | 0.958 | 0.866 | 0.000 |
| C2 | 0.792 | 0.958 | 0.958 | 0.873 | 0.000 |

McNemar: C0 vs C1 → b=1, c=5, p=0.219 (direção C1>C0, não-significante **por tamanho de amostra**). C1 vs C2 → b=1, c=1, p=1.0 (empate top-1; o re-ranking corrigiu uma pergunta e regrediu outra). Este resultado se manteve estável mesmo após (a) elevar a cobertura de bindings de 12 para 77 datasets e (b) tornar o sinal de grão **direcional** (penalizar grão agregado quando a pergunta pede grão fino).

**Leitura:** o padrão é exatamente o previsto por H1/H2a — salto grande C0→C1, delta nulo C1→C2 numa amostra pequena com teto alto. **24 perguntas não têm poder** para concluir nada; servem só para validar o harness. O estudo real exige o gold set de 300–500.

**Error analysis que o piloto já entrega** (via `ontology_boost.explicar`): o par de confusão `ear-diario-por-reservatorio` vs `ear-diario-por-ree-reservatorio-equivalente-de-energia` sobrevive ao boost porque "reservatório" e "reservatório equivalente (REE)" são quase idênticos lexicalmente e a ontologia atual não distingue reservatório físico de REE no texto da pergunta. É um caso-exemplo de limite da ontologia a documentar — e um candidato a `value_aliases` mais finos no contrato.

**Nota metodológica:** a tentação de ajustar os pesos do boost até C2 vencer nestas 24 perguntas foi deliberadamente **evitada** — seria calibrar no conjunto de teste. Os pesos devem ser fixados só no dev set (§6).

---

## 10. Ameaças à validade

- **Teto de performance (ceiling):** C1 já acerta ~0.96 no top-3. Há pouco espaço para C2 crescer exceto no top-1 e nos estratos difíceis. Por isso a estratificação de §4.1 é essencial — medir no agregado esconde o efeito.
- **Vazamento (leakage):** perguntas do gold que coincidem com `typicalQuestions`/`fewShotQueries` inflam C1 artificialmente. Filtrar.
- **Cobertura da ontologia:** a primeira rodada de `gerar_bindings` casava fenômeno em só **22 de 77 datasets**. Após enriquecer os `fenomenos`/`aliases` com o vocabulário real dos contratos (auditado via `measure_terms`/`family`) e adicionar inferência por nome do dataset como *fallback*, a cobertura subiu para **74/77 fenômeno (96%)**, **50/77 grão espacial** (os demais declaram `entity=dataset_row`, sem grão espacial — correto) e **77/77 perspectiva (100%)**. Os 3 datasets sem fenômeno (`classificacao-evt`, `uge_opera_csi`, `usi_servicoancilar_sandbox`) são jargão interno/sandbox sem `measure_terms` — não mapeáveis sem correção na fonte. Duas leituras, ambas reportáveis:
  1. *Limitação do estudo:* onde o binding é nulo, C2 degenera em C1 — o teto do efeito é limitado pela cobertura.
  2. *Achado:* a ontologia **expõe lacunas de completude dos contratos** que o BM25 mascara. Ex.: `ear-diario-por-ree-...` declara `row_grain.entity='dataset_row'` em vez de `ree`; o BM25 roteia mesmo assim, mas a ontologia só consegue aplicar o sinal de grão depois de inferir o grão pelo nome. A correção certa é **na fonte** (preencher `semantics` no contrato), não aumentar pesos do boost. Isto é, por si, um resultado sobre a qualidade da camada semântica.
- **Generalização:** 1 órgão, 78 datasets, português. Não generaliza para catálogos multi-órgão nem outros idiomas sem replicação.
- **Operacionalização de C0:** "schema puro" é uma escolha; um C0 que mantivesse descrições de coluna seria um piso mais alto. Reportar a definição exata (feito em `conditions._degradar_para_c0`).

---

## 11. Trabalho futuro

- Estender a ontologia ao eixo de **grandezas na validação** (checar aditividade cross-dataset em `executar_sql`, não só no roteamento).
- Experimento de **geração de SQL** com joins guiados pela hierarquia `parteDe` (perguntas de composição).
- Comparar o re-ranking determinístico (atual) com um **retriever denso ontologia-aware** (embeddings dos conceitos).
- Replicar em um catálogo multi-órgão para testar generalização.

---

## Referências

- Sequeda, Allemang et al. — benchmark KG vs SQL para QA sobre bancos empresariais ([arXiv 2311.07509](https://arxiv.org/html/2311.07509)).
- Aumento de acurácia de QA com ontologias ([arXiv 2405.11706](https://arxiv.org/html/2405.11706v1); versão Springer, +21,6 p.p.).
- Agente mediado por camada semântica para NL2SQL empresarial ([arXiv 2606.31041](https://arxiv.org/pdf/2606.31041)).
- Conhecimento de domínio em agentes NL2SQL — ganhos no BIRD/Spider 2.0 ([arXiv 2602.13521](https://arxiv.org/html/2602.13521v1)).
- Semantic layer vs. text-to-SQL — contraponto ([dbt, 2026](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)).
- Métricas EX/VES: benchmarks BIRD e Spider.

*Todo o conteúdo das fontes externas foi parafraseado e resumido; nenhuma reprodução verbatim acima de limites de licenciamento.*
