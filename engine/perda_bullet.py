"""Modelagem de perdas em cenário único ("bullet") para apresentação em
comitê — mesmo conceito da planilha de referência usada pelo time (PL
Inicial -> Rentabilidade -> Perdas -> Custos -> Remuneração -> PL Final,
com o PL Final distribuído pela cascata Sênior -> Mezanino -> Júnior),
recalibrado com duas correções em relação ao original:

1. CDI não é mais contado em dobro. A planilha original guardava o custo
   já cheio (CDI + spread) e depois multiplicava por (1+CDI) de novo na
   hora de calcular a remuneração -- isso inflava a remuneração de Sênior
   e Mezanino em ~2x e, por tabela, deixava os limiares de perda de cada
   cenário otimistas demais. Aqui CDI e spread entram como grandezas
   separadas (mesma convenção do Simulador de Estruturação) e a
   remuneração é a taxa cheia (CDI+spread) composta uma única vez.

2. Período explícito. Em vez de misturar uma "CDI do período" solta com
   taxas "(aa)", o prazo do fundo é um parâmetro explícito (em meses) e
   toda taxa anual é composta pro prazo real -- deixa de depender de
   presumir (sem declarar) que o período é sempre 12 meses.

O modelo continua sendo de UM ÚNICO PERÍODO (bullet): a carteira é cedida
uma vez, rende a taxa de cessão o prazo inteiro, e o PL Final resultante é
distribuído de uma vez pela cascata -- não é o motor de amortização mês a
mês do Simulador de Estruturação (`engine/waterfall.py`). É o modelo certo
para a pergunta "quanto de perda a estrutura aguenta", não para desenhar o
cronograma de pagamento.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ParametrosBullet:
    pl_inicial: float
    pct_senior: float          # fração do PL, ex.: 0.75
    pct_mezanino: float        # fração do PL, ex.: 0.10 (júnior = resíduo)
    pct_caixa: float           # fração do PL alocada em caixa (não em DCs)
    prazo_meses: float         # horizonte único modelado (o "bullet")
    taxa_cessao_am: float      # taxa de cessão sobre a carteira, ao mês
    cdi_aa: float              # CDI projetado, % a.a. (ex.: 0.14)
    ajuste_curva_aa: float = 0.0   # prêmio/desconto sobre o CDI projetado
    spread_senior_aa: float = 0.02     # SÓ o spread, sem embutir CDI
    spread_mezanino_aa: float = 0.03   # idem
    custo_fidc_aa: float = 0.0135      # taxa de administração/custos, % PL a.a.
    perda_base_pct: float = 0.005      # perda assumida no cenário Base

    @property
    def pct_junior(self) -> float:
        return max(0.0, 1 - self.pct_senior - self.pct_mezanino)

    @property
    def cota_senior(self) -> float:
        return self.pl_inicial * self.pct_senior

    @property
    def cota_mezanino(self) -> float:
        return self.pl_inicial * self.pct_mezanino

    @property
    def cota_junior(self) -> float:
        return self.pl_inicial * self.pct_junior

    @property
    def cdi_efetivo_aa(self) -> float:
        """CDI projetado + ajuste de curva -- mesma convenção do Simulador."""
        return self.cdi_aa + self.ajuste_curva_aa

    @property
    def carteira_dcs(self) -> float:
        return self.pl_inicial * (1 - self.pct_caixa)

    def _compor(self, taxa_aa: float) -> float:
        """Composição de uma taxa anual pelo prazo real do fundo (em vez de
        presumir 12 meses)."""
        return (1 + taxa_aa) ** (self.prazo_meses / 12) - 1

    @property
    def taxa_cessao_periodo(self) -> float:
        """Taxa de cessão (a.m.) composta pelo prazo inteiro, em meses."""
        return (1 + self.taxa_cessao_am) ** self.prazo_meses - 1

    @property
    def cdi_periodo(self) -> float:
        return self._compor(self.cdi_efetivo_aa)

    @property
    def custo_senior_aa(self) -> float:
        return self.cdi_efetivo_aa + self.spread_senior_aa

    @property
    def custo_mezanino_aa(self) -> float:
        return self.cdi_efetivo_aa + self.spread_mezanino_aa

    @property
    def remuneracao_senior(self) -> float:
        """Remuneração contratual da Sênior no período -- taxa cheia
        (CDI+spread) composta UMA vez pelo prazo, sem multiplicar o CDI de
        novo. Fixa: não depende da perda (é subtraída antes da cascata,
        que depois aloca o que sobrar pelo valor de face das cotas)."""
        return self._compor(self.custo_senior_aa) * self.cota_senior

    @property
    def remuneracao_mezanino(self) -> float:
        return self._compor(self.custo_mezanino_aa) * self.cota_mezanino

    @property
    def remuneracao_total(self) -> float:
        return self.remuneracao_senior + self.remuneracao_mezanino

    @property
    def custo_fundo(self) -> float:
        """Taxa de administração/custos do FIDC -- pro-rata linear pelo
        prazo (convenção comum para taxas de administração; não composta)."""
        return self.custo_fidc_aa * (self.prazo_meses / 12) * self.pl_inicial


def calcular_cenario(p: ParametrosBullet, perda_pct: float) -> dict:
    """Calcula todas as linhas do modelo para uma taxa de perda dada
    (perda_pct: fração da carteira de DCs perdida ao longo do período)."""
    perdas_valor = perda_pct * p.carteira_dcs
    rentabilidade = ((p.carteira_dcs - perdas_valor) * p.taxa_cessao_periodo
                     + p.pl_inicial * p.pct_caixa * p.cdi_periodo)
    pl_final = (p.pl_inicial + rentabilidade - perdas_valor - p.custo_fundo
               - p.remuneracao_total)

    sr_final = min(p.cota_senior, max(0.0, pl_final))
    meza_final = min(p.cota_mezanino, max(0.0, pl_final - p.cota_senior))
    jr_final = max(0.0, pl_final - p.cota_senior - p.cota_mezanino)

    return {
        "perda_pct": perda_pct,
        "perdas_valor": perdas_valor,
        "rentabilidade": rentabilidade,
        "custo_fundo": p.custo_fundo,
        "remuneracao_total": p.remuneracao_total,
        "pl_final": pl_final,
        "sr_final": sr_final,
        "meza_final": meza_final,
        "jr_final": jr_final,
        "pct_sr": (sr_final / p.cota_senior) if p.cota_senior else None,
        "pct_meza": (meza_final / p.cota_mezanino) if p.cota_mezanino else None,
        "pct_jr": (jr_final / p.cota_junior) if p.cota_junior else None,
        "retorno_jr": (jr_final / p.cota_junior - 1) if p.cota_junior else None,
    }


def perda_para_pl_final(p: ParametrosBullet, pl_final_alvo: float) -> float | None:
    """Resolve algebricamente (fórmula fechada) a taxa de perda sobre a
    carteira de DCs necessária para que o PL Final atinja um valor-alvo.

    PL Final é LINEAR em perda_pct (dado o restante fixo), então a solução
    é direta -- sem precisar de busca iterativa (Goal Seek) como a
    planilha original fazia manualmente antes de ter a fórmula fechada.
    Retorna None se a carteira de DCs for zero (sem perda possível)."""
    if p.carteira_dcs == 0:
        return None
    constante = (p.pl_inicial + p.carteira_dcs * p.taxa_cessao_periodo
                + p.pl_inicial * p.pct_caixa * p.cdi_periodo
                - p.custo_fundo - p.remuneracao_total)
    denom = p.carteira_dcs * (1 + p.taxa_cessao_periodo)
    return (constante - pl_final_alvo) / denom


def pontos_de_ruptura(p: ParametrosBullet) -> list[dict]:
    """Os 4 cenários nomeados: Base (perda assumida) + os 3 limiares que a
    estrutura atual sofre, na ordem de severidade. Generalizado por
    recovery-rate, não hardcoded por coluna como na planilha original --
    se a estrutura mudar (%s das classes), os limiares recalculam certo."""
    cenarios = [
        ("Base", "Perda assumida/histórica", p.perda_base_pct),
    ]

    alvo_pessimista = p.cota_senior + p.cota_mezanino + p.cota_junior
    perda_pess = perda_para_pl_final(p, alvo_pessimista)
    cenarios.append((
        "Pessimista",
        "Perda que zera o retorno da Júnior (ela recebe só o capital de volta)",
        perda_pess,
    ))

    alvo_stress = p.cota_senior + p.cota_mezanino
    perda_stress = perda_para_pl_final(p, alvo_stress)
    cenarios.append((
        "Stress",
        "Perda que zera a Júnior por completo",
        perda_stress,
    ))

    alvo_catastrofe = p.cota_senior
    perda_catastrofe = perda_para_pl_final(p, alvo_catastrofe)
    cenarios.append((
        "Catástrofe",
        "Perda que zera a Mezanino — a Sênior está no limiar de ser afetada",
        perda_catastrofe,
    ))

    resultado = []
    for nome, descricao, perda in cenarios:
        if perda is None:
            continue
        r = calcular_cenario(p, perda)
        resultado.append({"nome": nome, "descricao": descricao, **r})
    return resultado


def curva_perda(p: ParametrosBullet, passos: int = 101) -> pd.DataFrame:
    """Curva contínua de perda (0% a 100% da carteira) x recuperação por
    classe -- mostra de forma suave onde cada ruptura acontece, em vez de
    só os 4 pontos discretos dos cenários nomeados."""
    perdas = np.linspace(0, 1, passos)
    linhas = [calcular_cenario(p, x) for x in perdas]
    return pd.DataFrame(linhas)
