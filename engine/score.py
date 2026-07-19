"""Score de estruturabilidade do originador.

Nota 0–100 ponderando os fatores que o comitê olha antes de alocar capital
como cotista sênior. Pesos e réguas são parametrizáveis — ajuste à política
de crédito do banco. Toda decisão fica registrada (detalhe por fator).
"""

PESOS = {
    "inadimplencia": 0.30,
    "concentracao": 0.20,
    "historico": 0.15,
    "volume": 0.20,
    "prazo": 0.15,
}


def _regua(valor, faixas):
    """faixas: lista de (limite_superior, nota). Última faixa pega o resto."""
    for limite, nota in faixas:
        if valor <= limite:
            return nota
    return faixas[-1][1]


def calcular_score(o: dict) -> dict:
    inad = float(o.get("inadimplencia_hist") or 0)
    conc = float(o.get("concentracao_top10") or 0)
    anos = float(o.get("anos_operacao") or 0)
    vol = float(o.get("volume_mensal") or 0)
    prazo = float(o.get("prazo_medio_dias") or 0)

    notas = {
        "inadimplencia": _regua(inad, [(1, 100), (2, 85), (3.5, 65), (5, 40), (8, 20), (999, 5)]),
        "concentracao": _regua(conc, [(20, 100), (35, 80), (50, 60), (65, 35), (100, 15)]),
        "historico": _regua(-anos, [(-8, 100), (-5, 85), (-3, 65), (-1.5, 40), (0, 15)]),
        "volume": _regua(-vol, [(-30e6, 100), (-15e6, 85), (-8e6, 65), (-3e6, 40), (0, 15)]),
        "prazo": _regua(prazo, [(60, 100), (90, 85), (120, 65), (180, 45), (999, 25)]),
    }
    score = round(sum(notas[k] * PESOS[k] for k in PESOS), 1)

    # Recomendação de subordinação mínima: base 2.5x a perda esperada,
    # com piso de 15% e ajuste por concentração.
    sub_min = max(15.0, round(inad * 2.5 + max(0, (conc - 30)) * 0.15, 1))

    if score >= 75:
        veredicto = "Estruturável — condições padrão"
    elif score >= 55:
        veredicto = "Estruturável com mitigadores (subordinação/limites reforçados)"
    elif score >= 40:
        veredicto = "Marginal — exigir coobrigação ou garantias adicionais"
    else:
        veredicto = "Não recomendado no perfil atual"

    return {
        "score": score,
        "veredicto": veredicto,
        "subordinacao_minima_sugerida": sub_min,
        "detalhe": {k: {"nota": notas[k], "peso": PESOS[k]} for k in PESOS},
    }
