"""Nota de crédito interna por classe — régua comparável para comitê e
para a conversa com investidor.

Combina duas leituras independentes e usa a mais conservadora:
1. Break-even determinístico: quantas vezes a inadimplência base a classe
   suporta antes de sofrer perda (busca binária sobre o stress).
2. Probabilidade de perda no Monte Carlo (se disponível): fração dos
   cenários em que a classe não recebe 100% do prometido.

NÃO é um rating de agência (S&P/Moody's/Fitch) — é uma régua interna do
banco para comparar classes e fundos entre si de forma consistente. As
faixas abaixo são ponto de partida e devem ser calibradas pela política de
crédito do banco.
"""

import pandas as pd

from .waterfall import stress_breakeven

ESCALA = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]

FAIXAS_BREAKEVEN = [  # (múltiplo mínimo da inadimplência base, nota)
    (8.0, "AAA"), (5.0, "AA"), (3.0, "A"), (2.0, "BBB"),
    (1.5, "BB"), (1.0, "B"), (0.0, "CCC"),
]

FAIXAS_PROB = [  # (probabilidade máxima de não-integridade, nota)
    (0.001, "AAA"), (0.005, "AA"), (0.01, "A"), (0.03, "BBB"),
    (0.07, "BB"), (0.15, "B"), (1.0, "CCC"),
]


def _nota_por_breakeven(mult):
    if mult is None:
        return None
    for minimo, nota in FAIXAS_BREAKEVEN:
        if mult >= minimo:
            return nota
    return "CCC"


def _nota_por_prob(prob):
    if prob is None or pd.isna(prob):
        return None
    for maximo, nota in FAIXAS_PROB:
        if prob <= maximo:
            return nota
    return "CCC"


def pior_nota(a, b):
    """A mais conservadora entre duas notas (ou a única disponível)."""
    if a is None:
        return b
    if b is None:
        return a
    return ESCALA[max(ESCALA.index(a), ESCALA.index(b))]


def classificar_classe(breakeven_mult, prob_nao_integral=None) -> dict:
    n1 = _nota_por_breakeven(breakeven_mult)
    n2 = _nota_por_prob(prob_nao_integral)
    nota = pior_nota(n1, n2) or "NR"
    fontes = []
    if n1:
        fontes.append(f"stress {breakeven_mult:.1f}x → {n1}")
    if n2:
        fontes.append(f"Monte Carlo {prob_nao_integral*100:.2f}% → {n2}")
    return dict(nota=nota,
               justificativa=" · ".join(fontes) or "dados insuficientes")


def classificar_estrutura(estrutura, mc_stats=None) -> pd.DataFrame:
    """Uma linha por classe não-residual. A residual (subordinada/equity)
    recebe 'NR' — mercado não costuma dar rating de crédito a equity."""
    prob_por_classe = {}
    if mc_stats is not None:
        for _, row in mc_stats.iterrows():
            prob_por_classe[row["classe"]] = row.get("prob_nao_integral")

    linhas = []
    for i, c in enumerate(estrutura.classes):
        if c.residual:
            linhas.append(dict(classe=c.nome, nota="NR",
                               justificativa="classe residual (equity) — "
                                             "não classificada"))
            continue
        be = stress_breakeven(estrutura, i)
        r = classificar_classe(be, prob_por_classe.get(c.nome))
        linhas.append(dict(classe=c.nome, breakeven=be, **r))
    return pd.DataFrame(linhas)
