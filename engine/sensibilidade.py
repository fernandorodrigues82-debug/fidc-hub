"""Análise de sensibilidade: varia cada parâmetro para cima e para baixo,
mede o impacto no resultado (TIR da júnior por padrão, ou o
break-even da classe mais sênior) e ordena — o clássico "tornado chart"
que responde "onde vale a pena negociar com o originador".
"""

from dataclasses import replace

import pandas as pd

from .waterfall import simular, stress_breakeven

PARAMS_SENSIVEIS = {
    "taxa_cessao_am": ("Taxa de cessão", 0.15),       # nome, variação relativa
    "inadimplencia_am": ("Inadimplência base", 0.30),
    "prazo_medio_meses": ("Prazo médio", 0.30),
    "meses_revolvencia": ("Revolvência", 0.30),
    "recuperacao": ("Taxa de recuperação", 0.30),
    "custos_aa": ("Custos do fundo", 0.30),
    "prepagamento_am": ("Pré-pagamento", 0.30),
}


def _metrica(e, alvo: str):
    r = simular(e)
    if alvo == "tir_sub":
        residual = e.classes[-1]
        aporte = e.pl_total * residual.pct
        return r.resumo["residual_sub"] / aporte if aporte else None
    if alvo == "breakeven_senior":
        return stress_breakeven(e, 0)
    if alvo == "perdas_pct_pl":
        return r.resumo["perdas_totais"] / e.pl_total
    raise ValueError(alvo)


def tornado(estrutura, alvo: str = "tir_sub") -> pd.DataFrame:
    """alvo: 'tir_sub' (múltiplo de retorno da júnior — rápido),
    'perdas_pct_pl' ou 'breakeven_senior' (mais lento: cada ponto já é
    uma busca binária de stress)."""
    base = _metrica(estrutura, alvo)
    linhas = []
    for campo, (nome, delta) in PARAMS_SENSIVEIS.items():
        valor_base = getattr(estrutura, campo)
        if valor_base in (0, None):
            continue
        baixo = valor_base * (1 - delta)
        alto = valor_base * (1 + delta)
        if campo in ("prazo_medio_meses", "meses_revolvencia"):
            baixo, alto = max(1, round(baixo)), max(1, round(alto))

        e_baixo = replace(estrutura, **{campo: baixo})
        e_alto = replace(estrutura, **{campo: alto})
        m_baixo = _metrica(e_baixo, alvo)
        m_alto = _metrica(e_alto, alvo)
        if m_baixo is None or m_alto is None or base is None:
            continue
        linhas.append(dict(
            parametro=nome, campo=campo, valor_base=valor_base,
            valor_baixo=baixo, valor_alto=alto,
            metrica_base=base, metrica_baixo=m_baixo, metrica_alto=m_alto,
            impacto=abs(m_alto - m_baixo)))
    df = pd.DataFrame(linhas)
    return df.sort_values("impacto", ascending=True).reset_index(drop=True)
