# FIDC Hub — Especificação do produto (MVP Streamlit)

Plataforma do **estruturador bancário** que prospecta originadores, investe como
cotista sênior e distribui cotas a terceiros. O MVP cobre os módulos 1 a 3 do
desenho aprovado: funil de originadores, simulador de estruturação e esteira de
constituição — todos com trilha de auditoria.

## Como rodar

```bash
pip install -r requirements.txt
streamlit run app.py
```

O banco SQLite (`fidc_hub.db`) é criado automaticamente com três originadores de
exemplo. Use `exemplo_carteira.csv` para testar a análise de carteira.

## Arquitetura

| Camada | MVP | Migração corporativa |
|---|---|---|
| UI | Streamlit multipage | Mesmo Streamlit atrás de SSO (Entra ID/OAuth via proxy reverso) |
| Dados | SQLite (`db.py`) | Postgres — trocar apenas `db.py` (mesmas funções) |
| Motores | `engine/score.py`, `engine/waterfall.py` | Idênticos; puros Python, testáveis isoladamente |
| Auditoria | Tabela `auditoria` (quem/quando/o quê) | Append-only + retenção conforme compliance |

A separação UI ↔ motores é proposital: o simulador e o score não conhecem o
Streamlit, então podem ser reusados em API, batch ou notebooks do banco.

## Módulo 1 — Funil de originadores

Etapas: Prospecção → Due diligence → Análise de carteira → Simulação/Comitê →
Aprovado/Recusado. Cadastro captura os dados que alimentam o **score de
estruturabilidade** (0–100), ponderando inadimplência histórica (30%),
concentração top-10 (20%), volume mensal (20%), tempo de operação (15%) e prazo
médio (15%). O score devolve veredicto e **subordinação mínima sugerida**
(≈ 2,5× a perda esperada, piso de 15%, ajuste por concentração). Pesos e réguas
ficam em `engine/score.py` para calibração à política de crédito do banco — e a
decomposição por fator é persistida, criando a trilha de decisão do comitê.

A aba de **análise de carteira** recebe o CSV da carteira histórica
(`sacado, valor, data_vencimento, dias_atraso`) e calcula concentração real,
tíquete, aging e a proxy de perda (90+), para confrontar com o declarado na
due diligence.

## Módulo 2 — Simulador de estruturação

Modelo mensal de pool amortizante com revolvência (`engine/waterfall.py`):
carteira rende a taxa de cessão; fração `1/prazo médio` (+ pré-pagamento) do
principal vence por mês; inadimplência (com recuperação parametrizável) incide
sobre os vencimentos; despesas saem antes de qualquer classe; na amortização o
caixa segue a cascata sênior → mezanino → subordinada, que fica com o residual.

Saídas de decisão: integridade da sênior, TIR por classe, perdas vs colchão de
subordinação e **break-even da sênior** (maior múltiplo da inadimplência base
que a estrutura suporta, por busca binária). A aprovação vira um **deal** com os
parâmetros congelados — a versão que o comitê viu — e move o originador para
"Aprovado". A aprovação é bloqueada se a sênior sofre perda no cenário simulado.

Limitações conscientes do modelo (evoluções): curvas de perda por safra em vez
de taxa constante; marcação de carteira comprada com desconto vs valor de face;
gatilhos dinâmicos (evento de avaliação quando subordinação < mínimo);
Monte Carlo além do stress determinístico.

## Módulo 3 — Esteira de constituição

Checklist por fase, pré-carregado com o rito regulatório: estruturação
(regulamento, contrato de cessão, elegibilidade), prestadores (administrador,
gestor com responsabilidade sobre lastro — RCVM 175 —, custodiante, cobrança,
auditor, registradora, rating), registro e oferta (CVM, RCVM 160, suitability
RCVM 30, **mapeamento de conflito de interesse** pelo acúmulo banco
sênior + distribuidor) e lançamento. Progresso e status são auditados; 100%
concluído marca o deal como "Lançado".

## Roadmap (fases seguintes do desenho aprovado)

1. **Operação multi-fundo**: ingestão do estoque da registradora, verificação
   de elegibilidade nas cessões, enquadramento, PDD, alertas cruzados de sacado.
2. **Portal do investidor**: dataroom, relatórios padronizados por classe,
   performance realizada vs simulada.
3. **Governança**: perfis de acesso (originação × comitê × distribuição),
   aprovações em duas alçadas, export da trilha de auditoria.

## Aviso

Modelos e checklists são ferramentas de apoio à decisão e não substituem
assessoria jurídica e a validação regulatória (CVM/ANBIMA) de cada estrutura.
