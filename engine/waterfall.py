"""Motor de simulação da estrutura do FIDC — N classes de cotas.

Estrutura de capital genérica: lista de classes ordenadas por senioridade
(1ª = mais sênior). A última classe é sempre a residual (subordinada/equity):
não tem taxa-alvo e recebe o que sobra depois de todas as outras.

Cascata mensal:
1. Carteira rende a taxa de cessão; fração 1/prazo_medio (+ pré-pagamento)
   do principal vence; inadimplência (com recuperação) incide sobre o que
   vence no mês.
2. Despesas saem do caixa antes de qualquer classe.
3. Revolvência: caixa líquido recompra direitos creditórios.
4. Amortização: pagamento SEQUENCIAL — cada classe só recebe depois de a
   anterior estar 100% amortizada. As classes não-residuais acumulam a
   taxa-alvo sobre o saldo devedor.

Modelo de decisão (comitê), não de precificação contábil.
"""

from dataclasses import dataclass, field

import numpy as np
import numpy_financial as npf
import pandas as pd


@dataclass
class Classe:
    nome: str
    pct: float                 # fração do PL
    taxa_am: float = 0.0       # taxa-alvo a.m. (ignorada na residual)
    residual: bool = False     # última classe: fica com o excedente


@dataclass
class Estrutura:
    pl_total: float = 100_000_000.0
    classes: list = field(default_factory=lambda: [
        Classe("Sênior", 0.75, 0.011),
        Classe("Mezanino", 0.10, 0.014),
        Classe("Subordinada", 0.15, 0.0, residual=True),
    ])
    taxa_cessao_am: float = 0.022
    prazo_medio_meses: int = 3
    meses_revolvencia: int = 24
    inadimplencia_am: float = 0.008
    prepagamento_am: float = 0.01
    recuperacao: float = 0.30
    custos_aa: float = 0.012
    stress: float = 1.0

    def __post_init__(self):
        soma = sum(c.pct for c in self.classes)
        if abs(soma - 1.0) > 1e-6:
            raise ValueError(f"Percentuais das classes somam {soma:.4f}; "
                             "devem somar 1.0")
        if not self.classes[-1].residual:
            raise ValueError("A última classe deve ser a residual "
                             "(subordinada).")

    @property
    def pct_residual(self) -> float:
        return self.classes[-1].pct


@dataclass
class Resultado:
    fluxo: pd.DataFrame
    por_classe: pd.DataFrame
    resumo: dict = field(default_factory=dict)


def simular(e: Estrutura) -> Resultado:
    n = e.meses_revolvencia + e.prazo_medio_meses * 3 + 6
    d = min(0.95, e.inadimplencia_am * e.stress)
    q = 1.0 / e.prazo_medio_meses
    despesa_mensal = e.pl_total * e.custos_aa / 12.0

    pagaveis = e.classes[:-1]          # com taxa-alvo, ordem de prioridade
    residual = e.classes[-1]
    saldos = [e.pl_total * c.pct for c in pagaveis]
    sub_inicial = e.pl_total * residual.pct

    carteira, caixa = e.pl_total, 0.0
    pagos = [0.0] * len(pagaveis)
    pago_residual = perdas_acum = 0.0
    linhas = []

    for m in range(1, n + 1):
        juros = carteira * e.taxa_cessao_am
        princ_venc = carteira * min(1.0, q + e.prepagamento_am)
        perda = (juros + princ_venc) * d
        recup = perda * e.recuperacao
        caixa += juros + princ_venc - perda + recup
        carteira -= princ_venc
        perdas_acum += perda - recup

        pago_desp = min(caixa, despesa_mensal)
        caixa -= pago_desp

        for i, c in enumerate(pagaveis):
            saldos[i] *= (1 + c.taxa_am)

        fase = "revolvência" if m <= e.meses_revolvencia else "amortização"
        amort = [0.0] * len(pagaveis)
        amort_res = 0.0

        if fase == "revolvência":
            carteira += caixa
            caixa = 0.0
        else:
            if m == n:  # fim do horizonte: liquida carteira remanescente
                caixa += carteira * (1 - d)
                carteira = 0.0
            for i in range(len(pagaveis)):       # sequencial por senioridade
                amort[i] = min(caixa, saldos[i])
                saldos[i] -= amort[i]
                caixa -= amort[i]
            if all(s <= 1e-6 for s in saldos):
                amort_res = caixa
                caixa = 0.0
        for i in range(len(pagaveis)):
            pagos[i] += amort[i]
        pago_residual += amort_res

        linha = dict(mes=m, fase=fase, carteira=carteira, caixa=caixa,
                     perda_mes=perda - recup, perdas_acum=perdas_acum,
                     pago_residual=amort_res, despesas=pago_desp)
        for i, c in enumerate(pagaveis):
            linha[f"saldo_{c.nome}"] = saldos[i]
            linha[f"pago_{c.nome}"] = amort[i]
        linhas.append(linha)
        if fase == "amortização" and carteira <= 1e-2 and caixa <= 1e-2:
            break

    fluxo = pd.DataFrame(linhas)

    def _tir(aporte, serie):
        try:
            r = npf.irr([-aporte] + list(serie))
            return None if r is None or np.isnan(r) else (1 + r) ** 12 - 1
        except Exception:
            return None

    por_classe = []
    for i, c in enumerate(pagaveis):
        aporte = e.pl_total * c.pct
        shortfall = max(0.0, float(fluxo[f"saldo_{c.nome}"].iloc[-1]))
        por_classe.append(dict(
            classe=c.nome, pct=c.pct, aporte=aporte, recebido=pagos[i],
            shortfall=shortfall, integra=shortfall < 1.0,
            tir_aa=_tir(aporte, fluxo[f"pago_{c.nome}"])))
    por_classe.append(dict(
        classe=residual.nome, pct=residual.pct, aporte=sub_inicial,
        recebido=pago_residual, shortfall=0.0, integra=True,
        tir_aa=_tir(sub_inicial, fluxo["pago_residual"])))
    pc = pd.DataFrame(por_classe)

    resumo = dict(
        subordinacao_inicial=residual.pct,
        perdas_totais=perdas_acum,
        perdas_vs_sub=perdas_acum / sub_inicial if sub_inicial else np.inf,
        senior_integra=bool(pc.iloc[0]["integra"]),
        senior_shortfall=float(pc.iloc[0]["shortfall"]),
        tir_senior_aa=pc.iloc[0]["tir_aa"],
        residual_sub=pago_residual,
        retorno_sub_multiplo=pago_residual / sub_inicial if sub_inicial
        else np.inf,
        todas_integras=bool(pc["integra"].all()),
        meses_simulados=len(fluxo),
    )
    return Resultado(fluxo=fluxo, por_classe=pc, resumo=resumo)


def stress_breakeven(e: Estrutura, indice_classe: int = 0,
                     max_mult: float = 30.0) -> float | None:
    """Maior multiplicador de inadimplência em que a classe (por índice de
    senioridade) ainda recebe 100%. None se nem o cenário base passa."""
    def ok(mult):
        e2 = Estrutura(**{**e.__dict__, "stress": mult})
        r = simular(e2)
        return bool(r.por_classe.iloc[indice_classe]["integra"])

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
