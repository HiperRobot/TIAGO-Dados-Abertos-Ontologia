# Protocolo — Experimento end-to-end: unidade e aditividade

Complementa `PROTOCOLO.md` (que trata do roteamento). Aqui a pergunta é outra e,
pela literatura, é onde a camada semântica mais deveria render: **a correção da
unidade e da aditividade do resultado de um SQL**.

## 1. O enquadramento correto (ler antes de tudo)

Uma leitura ingênua perguntaria "a ontologia melhora o servidor em unidade/aditividade?".
**Essa pergunta não se aplica**, e é importante dizer por quê, para o experimento ser honesto.

O servidor **já** decide unidade e aditividade de forma determinística, provada pelo
contrato, analisando o AST do SQL (`resposta/result_schema.py` + `resposta/additivity.py`).
O cliente LLM **não escreve** esses campos — o servidor os calcula e, quando o contrato não
prova, **se abstém** (`unit=null`, `unit_tier="unknown"`). Não há LLM errando unidade ali
para a ontologia corrigir.

Então a pergunta científica real é a **contrafactual**:

> **RQ-E2E:** Quanto do acerto de unidade/aditividade se deve à camada semântica?
> Um julgamento SEM contrato (como um LLM nu faria) erra; o contrato recupera;
> a ontologia adiciona algo sobre o contrato?

Isto espelha exatamente a literatura (KG/ontologia vs SQL direto): o ganho aparece quando
se compara o sistema assistido contra o não assistido — não dentro do sistema já assistido.

## 2. Condições (arms)

As três recebem o **mesmo SQL** e os **mesmos nomes de coluna do resultado**; muda só o
conhecimento disponível para julgar unidade e aditividade.

| Arm | Nome | Como julga unidade/aditividade | Papel |
|-----|------|-------------------------------|-------|
| **U0** | heurística-nome | só pelo nome da coluna (regex: `mwmed`→MWmed, `gwh`→GWh, `pct/taxa`→%…) e uma regra ingênua "SUM⇒aditivo". Sem contrato. | proxy de LLM nu / baseline |
| **U1** | contrato | `result_schema_do_sql` atual: AST + contrato, com tiers e abstenção | estado de produção |
| **U2** | contrato+ontologia | U1, e quando U1 **se abstém** (unit_tier=unknown / additive indecidido), consulta a ontologia: `fenomeno→grandeza→{unidade_nativa, aditividade_temporal}` via o binding do dataset | a proposta |

Decisões de desenho:
- **U0 é proxy, não um LLM real.** Rodar um LLM traria estocasticidade e custo sem mudar a
  conclusão qualitativa (LLM nu erra unidade/aditividade de forma conhecida). Se quiser o
  número real com LLM, U0 vira um arm extra com N seeds e temperatura 0; o harness aceita.
- **U2 é não-destrutivo sobre U1**: só age onde U1 abstém. Assim o experimento isola o que a
  ontologia *acrescenta*, sem risco de piorar o que o contrato já provava. Mede o
  **resgate de abstenções** — a hipótese mais plausível de ganho.

## 3. Métricas

Para cada coluna-medida do gold, comparar o julgado com o correto.

### 3.1 Unidade
- **Acurácia de unidade** = acertos / colunas-medida. Acerto = unidade igual à gold
  (normalizada: `MWmed`==`mwmed`).
- Decompor por tipo de desfecho:
  - **correto** — disse a unidade certa
  - **errado-confiante** — disse uma unidade, e estava errada (o pior caso)
  - **abstenção** — disse `unknown` (não errou nem acertou)

### 3.2 Aditividade
- **Acurácia de aditividade** = acertos de `additive` (bool) / colunas-medida.
- Mesma decomposição correto / errado-confiante / abstenção. Aqui o **errado-confiante
  positivo** é crítico: afirmar que algo é somável quando é estoque/preço é o erro que
  dobra valores silenciosamente.

### 3.3 Abstenção (a métrica que valoriza o design "só afirma o que prova")
- **Taxa de abstenção** por arm.
- **Abstenção correta**: absteve-se onde o gold também é incerto/ambíguo (bom).
- **Erro-confiante evitado**: casos em que U0 erra com confiança e U1/U2 se abstêm (o
  valor do fail-closed).
- **Resgate de U2**: abstenções de U1 que U2 converte em acerto (o ganho da ontologia) —
  vs. abstenções de U1 que U2 converte em **erro** (o risco da ontologia).

### 3.4 Comparação estatística
- McNemar pareado (mesmos itens) por métrica binária (acerto de unidade; acerto de
  aditividade), para U0↔U1 e U1↔U2. Reusa `metrics.mcnemar`.

## 4. Gold set

Formato (uma linha por consulta):
```json
{
  "sql": "SELECT ... AVG(TRY_CAST(val_cargaenergiamwmed AS DOUBLE)) AS carga_mwmed FROM ...",
  "colunas": ["id_subsistema", "carga_mwmed"],
  "esperado": {
    "carga_mwmed": {"unit": "MWmed", "additive": false}
  }
}
```
- Só as **colunas-medida** entram em `esperado`; dimensões/tempo são ignoradas na métrica.
- **Fonte dos SQLs:** `fewShotQueries[].sql` e `sql_patterns[].template` dos contratos — já
  são SQL válidos e com a unidade/aditividade conhecidas (o contrato declara). O gold de
  unidade sai do `output_unit`/`unit_native`/`quantity_kind` do próprio contrato, revisado
  à mão.
- **Casos difíceis a incluir de propósito:** energia armazenada (estoque, `additive:false`),
  carga em MWmed (potência média, `additive:false`), energia em GWh via `SUM(mwmed*24)/1000`
  (aditivo, unidade transformada), share/percentual (`%`), COUNT (sem unidade).
- **Vazamento:** diferente do roteamento, aqui o SQL não é "a pergunta" — o risco de
  vazamento é menor, mas registre a proveniência do SQL (contrato vs escrito à mão).

## 5. Hipóteses

- **H-E1:** U1 ≫ U0 em acurácia de unidade e de aditividade, e U1 tem **muito menos
  erro-confiante** (o contrato troca erro por abstenção). Forte esperado.
- **H-E2:** U2 ≥ U1: a ontologia **resgata** parte das abstenções de U1 (onde o contrato não
  tinha `quantity_kind`/`aggregation_mode` mas a ontologia sabe a grandeza do fenômeno),
  sem aumentar erro-confiante. Este é o ganho que a tese busca, e o lugar mais provável de
  encontrá-lo em todo o estudo.
- **H-E3 (nula plausível):** se os contratos já declaram `quantity_kind`/`unit_native` em
  todas as métricas, U1 não se abstém e U2 ≈ U1. Resultado também publicável: "a ontologia
  é redundante quando o contrato é completo" — e a cobertura de `quantity_kind` nos
  contratos passa a ser a variável explicativa.

## 6. Procedimento

```bash
python -m research.harness.run_unidade \
    --gold research/harness/GOLD_UNIDADE.json \
    --ontologia research/ontologia/ons_ontologia.json \
    --saida research/harness/rodada_unidade.json
```
Determinístico (sem LLM, sem rede). Reproduzível.

## 6-bis. Resultado piloto (6 colunas-medida — ilustrativo)

Rodado com `GOLD_UNIDADE.example.json`, incluindo casos adversariais (nome neutro, estoque com SUM, COUNT):

**Acurácia de unidade** — U0: 0.67 correto / 0.33 abstenção; **U1: 1.00**; **U2: 1.00**.
**Acurácia de aditividade** — U0: 0.67 correto / **0.33 errado-confiante**; **U1: 1.00**; **U2: 1.00**.

Leitura, já com o poder de um piloto pequeno:
- **H-E1 sustentada:** o contrato (U1) acerta tudo; o ganho sobre U0 está nos adversariais.
  - `valor` (alias neutro sobre carga): U0 abstém, U1 prova MWmed.
  - `total` = `SUM(ear_mwmes)` (estoque): **U0 crava additive=true (erro perigoso)**, U1 sabe que estoque não soma. É o caso-tese.
- **U0 erra aditividade com confiança (0.33)** pela regra ingênua "SUM ⇒ somável" — o comportamento de LLM nu que a literatura descreve.
- **H-E3 sustentada:** com contratos completos, U1 nunca abstém, então U2 não tem o que resgatar (`resgate_u2` = 0) e **U2 ≈ U1**. A ontologia é redundante quando o contrato é completo.
- **Achado de projeto (risco da ontologia):** na primeira versão, o resgate de U2 converteu um acerto de U1 em **erro** no caso `COUNT` (aplicou a grandeza do dataset a uma contagem, que não tem unidade física). Corrigido blindando COUNT do resgate. Lição para o paper: *resgate ontológico sobre abstenção é arriscado precisamente onde a abstenção do contrato era correta* — o fail-closed do contrato às vezes é mais sábio que o "preenchimento" da ontologia.

**Ressalva:** 6 colunas não têm poder estatístico (todos os McNemar deram p ≥ 0.5). Serve para validar o harness e ilustrar os mecanismos. O estudo real precisa de dezenas de consultas, com peso nos casos adversariais e nos datasets de `quantity_kind` incompleto (onde U1 abstém e U2 tem chance de resgatar de verdade).

## 7. Ameaça à validade específica

- **U0 é um proxy heurístico, não um LLM.** A conclusão U1≫U0 vale como "contra um julgador
  sem contrato"; o número exato contra um LLM real depende do modelo. Declarar isso.
- **O gold de unidade vem do contrato.** Como o contrato é a fonte de verdade do servidor,
  há circularidade parcial: U1 é avaliado contra a mesma fonte que usa. Mitigação: revisão
  humana do gold e inclusão de casos onde o contrato é sabidamente incompleto (para U1 ter
  chance de abster/errar e U2 de resgatar).
