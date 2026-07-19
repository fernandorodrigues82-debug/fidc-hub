"""Motor de simulação da estrutura do FIDC.

Modelo mensal simplificado de pool amortizante com fase de revolvência:

1. A carteira rende a taxa de cessão (a.m.) e uma fração 1/prazo_medio do
   principal vence por mês (+ pré-pagamento).
2. Inadimplência incide sobre juros e principal vencidos no mês
   (com taxa de recuperação parametrizável).
3. Despesas saem do caixa antes de qualquer classe.
4. Durante a revolvência, o caixa líquido recompra direitos creditórios.
5. Na amortização, o caixa segue a cascata: sênior → mezanino → subordinada.
6. As cotas sênior e mezanino acumulam a taxa-alvo sobre o saldo devedor;
   a subordinada fica com o residual (equity da estrutura).

É um modelo de decisão (comitê), não de precificação contábil — em produção,
plugar curvas de safra reais e o estoque efetivo da registradora.
"""

from dataclasses import dataclass, field

import numpy as np
import numpy_financial as npf
import pandas as pd


@dataclass
class Estrutura:
    pl_total: float = 100_000_000.0
    pct_senior: float = 0.75          # fração do PL
    pct_mezanino: float = 0.10        # fração do PL (sub = resto)
    taxa_senior_am: float = 0.011     # taxa-alvo a.m. da sênior
    taxa_mezanino_am: float = 0.014
    taxa_cessao_am: float = 0.022     # rendimento implícito da carteira
    prazo_medio_meses: int = 3
    meses_revolvencia: int = 24
    inadimplencia_am: float = 0.008   # perda base sobre vencimentos do mês
    prepagamento_am: float = 0.01     # fração extra do principal antecipada
    recuperacao: float = 0.30         # % recuperado dos créditos vencidos
    custos_aa: float = 0.012          # despesas do fundo (% PL a.a.)
    stress: float = 1.0               # multiplicador sobre a inadimplência

    @property
    def pct_sub(self) -> float:
        return 1.0 - self.pct_senior - self.pct_mezanino


@dataclass
class Resultado:
    fluxo: pd.DataFrame
    resumo: dict = field(default_factory=dict)


def simular(e: Estrutura) -> Resultado:
    n = e.meses_revolvencia + e.prazo_medio_meses * 3 + 6
    d = min(0.95, e.inadimplencia_am * e.stress)
    q = 1.0 / e.prazo_medio_meses
    despesa_mensal = e.pl_total * e.custos_aa / 12.0

    carteira = e.pl_total
    caixa = 0.0
    saldo_sen = e.pl_total * e.pct_senior
    saldo_mez = e.pl_total * e.pct_mezanino
    sub_inicial = e.pl_total * e.pct_sub

    linhas = []
    pago_sen = pago_mez = pago_sub = perdas_acum = 0.0

    for m in range(1, n + 1):
        # --- carteira gera caixa
        juros = carteira * e.taxa_cessao_am
        princ_venc = carteira * min(1.0, q + e.prepagamento_am)
        perda = (juros + princ_venc) * d
        recup = perda * e.recuperacao
        caixa += juros + princ_venc - perda + recup
        carteira -= princ_venc
        perdas_acum += perda - recup

        # --- despesas
        pago_desp = min(caixa, despesa_mensal)
        caixa -= pago_desp

        # --- acúmulo da taxa-alvo
        saldo_sen *= (1 + e.taxa_senior_am)
        saldo_mez *= (1 + e.taxa_mezanino_am)

        fase = "revolvência" if m <= e.meses_revolvencia else "amortização"
        amort_sen = amort_mez = amort_sub = 0.0

        if fase == "revolvência":
            # reinveste caixa livre em novos direitos creditórios
            carteira += caixa
            caixa = 0.0
        else:
            if m == n:  # fim do horizonte: liquida a carteira remanescente
                caixa += carteira * (1 - d)
                carteira = 0.0
            amort_sen = min(caixa, saldo_sen)
            saldo_sen -= amort_sen
            caixa -= amort_sen
            amort_mez = min(caixa, saldo_mez)
            saldo_mez -= amort_mez
            caixa -= amort_mez
            if saldo_sen <= 1e-6 and saldo_mez <= 1e-6:
                amort_sub = caixa
                caixa = 0.0
        pago_sen += amort_sen
        pago_mez += amort_mez
        pago_sub += amort_sub

        linhas.append(dict(
            mes=m, fase=fase, carteira=carteira, caixa=caixa,
            perda_mes=perda - recup, perdas_acum=perdas_acum,
            saldo_senior=saldo_sen, saldo_mezanino=saldo_mez,
            pago_senior=amort_sen, pago_mezanino=amort_mez,
            pago_sub=amort_sub, despesas=pago_desp,
        ))
        if fase == "amortização" and carteira <= 1e-2 and caixa <= 1e-2:
            break

    fluxo = pd.DataFrame(linhas)

    # ---- métricas por classe
    def _tir(aporte, df_col):
        fluxos = [-aporte] + list(fluxo[df_col])
        try:
            r = npf.irr(fluxos)
            return None if r is None or np.isnan(r) else (1 + r) ** 12 - 1
        except Exception:
            return None

    aporte_sen = e.pl_total * e.pct_senior
    aporte_mez = e.pl_total * e.pct_mezanino
    shortfall_sen = max(0.0, float(fluxo["saldo_senior"].iloc[-1]))
    shortfall_mez = max(0.0, float(fluxo["saldo_mezanino"].iloc[-1]))

    resumo = dict(
        subordinacao_inicial=e.pct_sub,
        perdas_totais=perdas_acum,
        perdas_vs_sub=perdas_acum / sub_inicial if sub_inicial else np.inf,
        senior_recebido=pago_sen,
        senior_shortfall=shortfall_sen,
        senior_integra=shortfall_sen < 1.0,
        mezanino_shortfall=shortfall_mez,
        residual_sub=pago_sub,
        retorno_sub_multiplo=pago_sub / sub_inicial if sub_inicial else np.inf,
        tir_senior_aa=_tir(aporte_sen, "pago_senior"),
        tir_mezanino_aa=_tir(aporte_mez, "pago_mezanino"),
        meses_simulados=len(fluxo),
    )
    return Resultado(fluxo=fluxo, resumo=resumo)


def stress_breakeven(e: Estrutura, alvo: str = "senior",
                     max_mult: float = 30.0) -> float | None:
    """Maior multiplicador de inadimplência em que a classe-alvo ainda
    recebe 100% (busca binária). Retorna None se nem o cenário base passa."""
    campo = "senior_shortfall" if alvo == "senior" else "mezanino_shortfall"

    def ok(mult):
        e2 = Estrutura(**{**e.__dict__, "stress": mult})
        return simular(e2).resumo[campo] < 1.0

    if not ok(1.0):
        return None
    lo, hi = 1.0, max_mult
    if ok(hi):
        return hi
    for _ in range(30):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return round(lo, 2)
