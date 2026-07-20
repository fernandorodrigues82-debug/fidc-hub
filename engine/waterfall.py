"""Motor de simulação da estrutura do FIDC — N classes de cotas.

Estrutura de capital genérica: lista de classes ordenadas por senioridade
(1ª = mais sênior). A última classe é sempre a residual (subordinada/equity):
não tem taxa-alvo e recebe o que sobra depois de todas as outras.

Cascata mensal, em até quatro fases:
1. Rampa (opcional): o capital é chamado (integralizado) progressivamente
   em vez de 100% no mês 1 — cada classe só passa a render sobre o que já
   foi chamado, evitando "carry" negativo de capital captado e parado.
2. Revolvência: caixa (coleta + capital recém-chamado) recompra direitos
   creditórios.
3. Carência (opcional): reinvestimento para — o fundo não compra mais
   recebíveis — mas a amortização ainda não começou; o caixa apenas se
   acumula como reserva.
4. Amortização: pagamento SEQUENCIAL — cada classe só recebe depois de a
   anterior estar 100% amortizada. As classes não-residuais acumulam a
   taxa-alvo sobre o saldo já chamado.

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
    sub_minima: float | None = None  # gatilho: índice mínimo de subordinação
    # (ativos - dívida sênior/mez) / ativos. Se furar antes da amortização,
    # dispara evento de avaliação: para de reinvestir e amortiza antecipado.
    meses_rampa: int = 0        # meses até 100% do PL estar chamado/investido
    meses_carencia: int = 0     # meses entre o fim da revolvência e o
    # início da amortização, sem reinvestir nem amortizar (só acumula caixa)

    def __post_init__(self):
        soma = sum(c.pct for c in self.classes)
        if abs(soma - 1.0) > 1e-6:
            raise ValueError(f"Percentuais das classes somam {soma:.4f}; "
                             "devem somar 1.0")
        if not self.classes[-1].residual:
            raise ValueError("A última classe deve ser a residual "
                             "(subordinada).")
        if self.meses_rampa and self.meses_rampa > self.meses_revolvencia:
            raise ValueError("A rampa de integralização não pode ser mais "
                             "longa que a revolvência "
                             f"({self.meses_rampa} > "
                             f"{self.meses_revolvencia} meses).")

    @property
    def pct_residual(self) -> float:
        return self.classes[-1].pct


@dataclass
class Resultado:
    fluxo: pd.DataFrame
    por_classe: pd.DataFrame
    resumo: dict = field(default_factory=dict)


def simular(e: Estrutura, vetor_inadimplencia=None) -> Resultado:
    """vetor_inadimplencia: opcional, taxa de perda por mês (sobrepõe a
    inadimplência base x stress — usado pelo Monte Carlo)."""
    n = (e.meses_revolvencia + e.meses_carencia
        + e.prazo_medio_meses * 3 + 6)
    d = min(0.95, e.inadimplencia_am * e.stress)
    q = 1.0 / e.prazo_medio_meses
    despesa_mensal = e.pl_total * e.custos_aa / 12.0

    pagaveis = e.classes[:-1]          # com taxa-alvo, ordem de prioridade
    residual = e.classes[-1]
    pct_pagaveis = [c.pct for c in pagaveis]
    sub_inicial = e.pl_total * residual.pct

    saldos = [0.0] * len(pagaveis)     # capital JÁ chamado de cada classe
    saldo_residual_chamado = 0.0
    chamado_acum = 0.0                 # capital total já chamado (todas as classes)

    carteira, caixa = 0.0, 0.0
    pagos = [0.0] * len(pagaveis)
    chamadas = [[] for _ in pagaveis]  # série mensal de capital chamado
    chamadas_residual = []
    pago_residual = perdas_acum = 0.0
    gatilho_mes = None
    linhas = []

    for m in range(1, n + 1):
        # ---- chamada de capital (integralização progressiva, se houver rampa)
        if e.meses_rampa and m <= e.meses_rampa:
            alvo_chamado = e.pl_total * min(1.0, m / e.meses_rampa)
        else:
            alvo_chamado = e.pl_total
        nova_chamada = max(0.0, alvo_chamado - chamado_acum)
        chamado_acum += nova_chamada
        chamada_res_mes = nova_chamada * residual.pct
        saldo_residual_chamado += chamada_res_mes
        chamadas_residual.append(chamada_res_mes)
        for i, pct in enumerate(pct_pagaveis):
            c_i = nova_chamada * pct
            saldos[i] += c_i
            chamadas[i].append(c_i)
        carteira += nova_chamada  # capital chamado compra recebíveis direto

        d_m = d if vetor_inadimplencia is None else min(
            0.95, float(vetor_inadimplencia[m - 1]))
        juros = carteira * e.taxa_cessao_am
        princ_venc = carteira * min(1.0, q + e.prepagamento_am)
        perda = (juros + princ_venc) * d_m
        recup = perda * e.recuperacao
        caixa += juros + princ_venc - perda + recup
        carteira -= princ_venc
        perdas_acum += perda - recup

        pago_desp = min(caixa, despesa_mensal)
        caixa -= pago_desp

        for i, c in enumerate(pagaveis):
            saldos[i] *= (1 + c.taxa_am)

        # índice de subordinação dinâmico: colchão sobre os ativos
        ativos = carteira + caixa
        divida = sum(saldos)
        indice_sub = (ativos - divida) / ativos if ativos > 1e-6 else 0.0

        fase_natural = ("revolvência" if m <= e.meses_revolvencia else
                        "carência" if m <= e.meses_revolvencia + e.meses_carencia
                        else "amortização")
        em_rampa = bool(e.meses_rampa and m <= e.meses_rampa)
        if (gatilho_mes is None and e.sub_minima is not None and not em_rampa
                and fase_natural in ("revolvência", "carência")
                and indice_sub < e.sub_minima):
            gatilho_mes = m  # evento de avaliação: amortização antecipada
        fase = "amortização" if gatilho_mes is not None else fase_natural
        if e.meses_rampa and m <= e.meses_rampa and fase == "revolvência":
            fase = "rampa"

        amort = [0.0] * len(pagaveis)
        amort_res = 0.0

        if fase in ("rampa", "revolvência"):
            carteira += caixa
            caixa = 0.0
        elif fase == "carência":
            pass  # não reinveste nem amortiza: caixa só se acumula
        else:  # amortização
            if m == n:  # fim do horizonte: liquida carteira remanescente
                caixa += carteira * (1 - d_m)
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
                     indice_subordinacao=indice_sub,
                     capital_chamado_mes=nova_chamada,
                     capital_chamado_acum=chamado_acum,
                     pago_residual=amort_res, despesas=pago_desp)
        for i, c in enumerate(pagaveis):
            linha[f"saldo_{c.nome}"] = saldos[i]
            linha[f"pago_{c.nome}"] = amort[i]
        linhas.append(linha)
        if fase == "amortização" and carteira <= 1e-2 and caixa <= 1e-2:
            break

    fluxo = pd.DataFrame(linhas)

    def _tir(serie_pago, serie_chamado):
        try:
            total_chamado = sum(serie_chamado)
            if not total_chamado:
                return None
            # Sem rampa (100% chamado no mês 1): replica exatamente a
            # convenção original — aporte num "tempo zero" anterior à
            # primeira atividade do mês 1 — para não alterar números já
            # existentes só por causa da generalização do modelo.
            if serie_chamado and abs(serie_chamado[0] - total_chamado) < 1e-6:
                fluxos = [-total_chamado] + list(serie_pago)
            else:
                fluxos = [p - c for p, c in zip(serie_pago, serie_chamado)]
            if all(abs(v) < 1e-9 for v in fluxos):
                return None
            r = npf.irr(fluxos)
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
            tir_aa=_tir(fluxo[f"pago_{c.nome}"], chamadas[i])))
    por_classe.append(dict(
        classe=residual.nome, pct=residual.pct, aporte=sub_inicial,
        recebido=pago_residual, shortfall=0.0, integra=True,
        tir_aa=_tir(fluxo["pago_residual"], chamadas_residual)))
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
        gatilho_mes=gatilho_mes,
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


def analise_suporte(e: Estrutura) -> dict:
    """Responde à pergunta do comitê: até quanto o fundo suporta antes de a
    classe mais sênior sofrer perda?

    Devolve o break-even em três leituras equivalentes:
    - múltiplo da inadimplência base;
    - inadimplência mensal absoluta correspondente;
    - perda acumulada total absorvida nesse limite (R$ e % do PL).
    """
    be = stress_breakeven(e, 0)
    if be is None:
        return dict(breakeven_mult=None)
    e_be = Estrutura(**{**e.__dict__, "stress": be})
    r_be = simular(e_be)
    perdas = r_be.resumo["perdas_totais"]
    return dict(
        breakeven_mult=be,
        inad_am_equivalente=e.inadimplencia_am * be,
        perda_maxima=perdas,
        perda_maxima_pct_pl=perdas / e.pl_total,
        gatilho_no_limite=r_be.resumo["gatilho_mes"],
    )


def curva_stress(e: Estrutura, max_mult: float | None = None,
                 pontos: int = 13) -> pd.DataFrame:
    """Varre multiplicadores de stress e devolve, por ponto, o percentual
    recuperado de cada classe e a perda total — base do gráfico de suporte."""
    if max_mult is None:
        be = stress_breakeven(e, 0)
        max_mult = max(6.0, (be or 4.0) * 1.5)
    linhas = []
    for mult in np.linspace(1.0, max_mult, pontos):
        r = simular(Estrutura(**{**e.__dict__, "stress": float(mult)}))
        linha = dict(stress=round(float(mult), 2),
                     perdas_pct_pl=r.resumo["perdas_totais"] / e.pl_total)
        for _, row in r.por_classe.iterrows():
            if row["classe"] == e.classes[-1].nome:  # residual: vs aporte
                linha[f"recup_{row['classe']}"] = min(
                    1.0, row["recebido"] / row["aporte"]) if row["aporte"] \
                    else 1.0
            else:
                alvo = row["recebido"] + row["shortfall"]
                linha[f"recup_{row['classe']}"] = (
                    row["recebido"] / alvo if alvo > 0 else 1.0)
        linhas.append(linha)
    return pd.DataFrame(linhas)


def montecarlo(e: Estrutura, n_sims: int = 500, vol: float = 0.5,
               rho: float = 0.6, seed: int = 42):
    """Distribuição de retornos por classe via Monte Carlo.

    A inadimplência mensal segue um processo lognormal com persistência
    AR(1): meses ruins tendem a ser seguidos de meses ruins (ciclo de
    crédito), em torno da inadimplência base x stress do cenário.

    vol: desvio da lognormal (0,3 = carteira estável; 1,0 = muito volátil).
    Retorna (df_sims, df_stats): resultados por simulação/classe e o resumo
    com percentis de TIR e probabilidades de perda.
    """
    rng = np.random.default_rng(seed)
    n = (e.meses_revolvencia + e.meses_carencia
        + e.prazo_medio_meses * 3 + 6)
    base = e.inadimplencia_am * e.stress

    linhas = []
    for s_i in range(n_sims):
        z = rng.standard_normal(n)
        x = np.empty(n)
        x[0] = z[0]
        for t in range(1, n):
            x[t] = rho * x[t - 1] + np.sqrt(1 - rho ** 2) * z[t]
        vetor = base * np.exp(vol * x - vol ** 2 / 2)
        r = simular(e, vetor_inadimplencia=vetor)
        for _, row in r.por_classe.iterrows():
            linhas.append(dict(
                sim=s_i, classe=row["classe"], tir_aa=row["tir_aa"],
                integra=bool(row["integra"]),
                perdeu_principal=row["recebido"] < row["aporte"] - 1.0))
    df = pd.DataFrame(linhas)

    stats = []
    for classe, g in df.groupby("classe", sort=False):
        tir = pd.to_numeric(g["tir_aa"], errors="coerce").dropna()
        stats.append(dict(
            classe=classe,
            tir_media=tir.mean() if len(tir) else np.nan,
            tir_p5=tir.quantile(0.05) if len(tir) else np.nan,
            tir_p50=tir.quantile(0.50) if len(tir) else np.nan,
            tir_p95=tir.quantile(0.95) if len(tir) else np.nan,
            prob_nao_integral=1.0 - g["integra"].mean(),
            prob_perda_principal=g["perdeu_principal"].mean()))
    ordem = [c.nome for c in e.classes]
    df_stats = (pd.DataFrame(stats).set_index("classe").loc[ordem]
                .reset_index())
    return df, df_stats


def estrutura_de_params(params: dict) -> Estrutura:
    """Reconstrói a Estrutura a partir do dict salvo no deal aprovado —
    usada pelo painel consolidado e pelo rating para recalcular métricas
    sem duplicar a definição da estrutura."""
    classes = [Classe(c["nome"], c["pct"], c.get("taxa_am", 0.0),
                      residual=c.get("residual", False))
              for c in params["classes"]]
    return Estrutura(
        pl_total=params["pl_total"], classes=classes,
        taxa_cessao_am=params["taxa_cessao_am"],
        prazo_medio_meses=params["prazo_medio_meses"],
        meses_revolvencia=params["meses_revolvencia"],
        inadimplencia_am=params["inadimplencia_am"],
        prepagamento_am=params.get("prepagamento_am", 0.01),
        recuperacao=params.get("recuperacao", 0.30),
        custos_aa=params.get("custos_aa", 0.012),
        stress=params.get("stress", 1.0),
        sub_minima=params.get("sub_minima"),
        meses_rampa=params.get("meses_rampa", 0),
        meses_carencia=params.get("meses_carencia", 0))
