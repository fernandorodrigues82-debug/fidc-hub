"""Calibra os parâmetros do Monte Carlo (volatilidade e persistência) a
partir da carteira histórica real do originador, em vez de valores
arbitrados no slider.

Ideia: para cada mês de vencimento já maturado (tem `dias_atraso`
preenchido), calculamos a fração do valor que está severamente atrasada
(>= limiar de dias) — isso dá uma série mensal de "taxa de perda" real.
Dessa série extraímos:
  - taxa_media_am: nível médio (pode sugerir a inadimplência base)
  - vol: dispersão dos desvios em log em torno da média — mesmo parâmetro
    que alimenta a lognormal do motor Monte Carlo (`engine.waterfall.montecarlo`)
  - rho: autocorrelação de 1 mês — persistência do ciclo de crédito

É uma aproximação (usa aging observado na data do relatório, não perda
líquida definitiva), mas já calibra a partir de dados reais em vez de
achismo — e o comitê consegue ver a série que gerou o número.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine.leitura_arquivos import data_br, numero_br

MIN_MESES_OK = 6      # confiável
MIN_MESES_MINIMO = 3  # abaixo disso, não calibra


@dataclass
class Calibracao:
    ok: bool
    motivo: str = ""
    n_meses: int = 0
    taxa_media_am: float = 0.0
    vol: float = 0.5
    rho: float = 0.5
    serie: pd.DataFrame = field(default_factory=pd.DataFrame)
    periodo: str = ""


def calibrar_de_carteira(cart: pd.DataFrame, limiar_dias: int = 90,
                         min_titulos_mes: int = 5) -> Calibracao:
    df = cart.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    faltam = {"valor", "data_vencimento", "dias_atraso"} - set(df.columns)
    if faltam:
        return Calibracao(ok=False,
                          motivo=f"Colunas ausentes para calibrar: "
                                 f"{', '.join(sorted(faltam))}.")

    df["valor"] = numero_br(df["valor"])
    df["data_vencimento"] = data_br(df["data_vencimento"])
    df["dias_atraso"] = numero_br(df["dias_atraso"])
    maturados = df.dropna(subset=["valor", "data_vencimento", "dias_atraso"])
    if maturados.empty:
        return Calibracao(ok=False,
                          motivo="Nenhum título com vencimento já maturado "
                                 "e `dias_atraso` preenchido — não dá para "
                                 "calibrar uma série temporal.")

    maturados = maturados.assign(
        safra=maturados["data_vencimento"].dt.to_period("M"))
    grp = maturados.groupby("safra")
    resumo = grp.apply(
        lambda g: pd.Series({
            "n_titulos": len(g),
            "valor_total": g["valor"].sum(),
            "valor_perda": g.loc[g["dias_atraso"] >= limiar_dias,
                                 "valor"].sum(),
        }), include_groups=False).reset_index()
    resumo = resumo[resumo["n_titulos"] >= min_titulos_mes].sort_values("safra")
    resumo["taxa"] = (resumo["valor_perda"] / resumo["valor_total"]).clip(
        lower=0.0003, upper=0.95)

    n_meses = len(resumo)
    if n_meses < MIN_MESES_MINIMO:
        return Calibracao(
            ok=False, n_meses=n_meses,
            motivo=f"Apenas {n_meses} mês(es) com volume suficiente de "
                   f"vencimentos maturados (mínimo {min_titulos_mes} "
                   f"títulos/mês). São necessários pelo menos "
                   f"{MIN_MESES_MINIMO} meses para calibrar.")

    taxa_media = float(np.average(resumo["taxa"],
                                  weights=resumo["valor_total"]))
    y = np.log(resumo["taxa"].values / taxa_media)
    vol = float(np.std(y, ddof=1)) if n_meses >= 2 else 0.5
    vol = float(np.clip(vol, 0.15, 1.5))

    if n_meses >= 3:
        rho = float(np.corrcoef(y[:-1], y[1:])[0, 1])
        rho = 0.0 if np.isnan(rho) else float(np.clip(rho, 0.0, 0.85))
    else:
        rho = 0.4  # default conservador com poucos pontos

    motivo = "" if n_meses >= MIN_MESES_OK else (
        f"Calibrado com apenas {n_meses} meses de histórico — trate como "
        f"indicativo. Confiabilidade melhora a partir de {MIN_MESES_OK} "
        f"meses maturados.")

    serie = resumo.assign(mes=resumo["safra"].astype(str))[
        ["mes", "n_titulos", "valor_total", "taxa"]]
    periodo = f"{serie['mes'].iloc[0]} a {serie['mes'].iloc[-1]}"

    return Calibracao(ok=True, motivo=motivo, n_meses=n_meses,
                      taxa_media_am=taxa_media, vol=round(vol, 3),
                      rho=round(rho, 3), serie=serie, periodo=periodo)
