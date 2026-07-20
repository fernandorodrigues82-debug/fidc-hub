"""Motor da operação corrente dos fundos: elegibilidade de cessões, PDD
(provisão para devedores duvidosos) e enquadramento de limites.

A idade dos títulos (`dias_atraso`) é sempre calculada dinamicamente a
partir de `data_vencimento` vs. a data de referência — não fica gravada
estática no banco. Assim, a carteira "envelhece" sozinha a cada vez que a
página é aberta, sem exigir novo upload para refletir o passar do tempo.
"""

import pandas as pd

from engine.leitura_arquivos import data_br

DEFAULT_CRITERIOS = dict(
    concentracao_max_sacado=15.0,   # % da carteira ativa do fundo
    prazo_max_dias=120,             # prazo máximo aceito na cessão
    regua_pdd=[                     # (dias_min, dias_max, % de provisão)
        (0, 30, 0.0), (31, 60, 3.0), (61, 90, 10.0),
        (91, 180, 50.0), (181, 99999, 100.0),
    ],
)


def _dias_atraso(data_vencimento, hoje):
    dias = (hoje - data_vencimento).dt.days
    return dias.clip(lower=0)


def validar_lote(df: pd.DataFrame, carteira_atual_sacado: dict,
                 pl_referencia: float, criterios: dict,
                 hoje=None) -> pd.DataFrame:
    """Marca cada título do lote como elegível ou não, considerando prazo
    máximo e concentração por sacado.

    A concentração usa o PL comprometido do fundo como base fixa — não a
    carteira ativa corrente. Se usássemos a carteira atual, o primeiro
    lote de um fundo novo (carteira ainda zerada) tornaria qualquer título
    grande "100% da carteira" e rejeitaria cessões legítimas só por causa
    da ordem de chegada."""
    hoje = hoje or pd.Timestamp.now().normalize()
    d = df.copy()
    d["data_vencimento"] = data_br(d["data_vencimento"])
    d["prazo_dias"] = (d["data_vencimento"] - hoje).dt.days

    limite_sacado = pl_referencia * criterios["concentracao_max_sacado"] / 100

    motivos, elegiveis = [], []
    acumulado_lote: dict = {}
    for _, row in d.iterrows():
        motivo = []
        if pd.isna(row["data_vencimento"]):
            motivo.append("data de vencimento inválida")
        elif row["prazo_dias"] < 0:
            motivo.append("título já vencido")
        elif row["prazo_dias"] > criterios["prazo_max_dias"]:
            motivo.append(f"prazo {row['prazo_dias']:.0f}d > máximo "
                          f"{criterios['prazo_max_dias']:.0f}d")
        sacado = row["sacado"]
        exposto = (carteira_atual_sacado.get(sacado, 0)
                  + acumulado_lote.get(sacado, 0) + row["valor"])
        if limite_sacado > 0 and exposto > limite_sacado:
            motivo.append(f"concentração do sacado excederia "
                          f"{criterios['concentracao_max_sacado']:.0f}% "
                          "do PL do fundo")
        ok = not motivo
        if ok:
            acumulado_lote[sacado] = acumulado_lote.get(sacado, 0) + row["valor"]
        elegiveis.append(ok)
        motivos.append("; ".join(motivo))
    d["elegivel"] = elegiveis
    d["motivo_inelegivel"] = motivos
    return d


def calcular_pdd(cessoes_ativas: pd.DataFrame, regua=None, hoje=None) -> dict:
    regua = regua or DEFAULT_CRITERIOS["regua_pdd"]
    hoje = hoje or pd.Timestamp.now().normalize()
    if cessoes_ativas.empty:
        return dict(total_pdd=0.0, total_carteira=0.0, pct_pdd=0.0,
                   breakdown=pd.Series(dtype=float))
    d = cessoes_ativas.copy()
    d["data_vencimento"] = pd.to_datetime(d["data_vencimento"])
    d["dias_atraso"] = _dias_atraso(d["data_vencimento"], hoje)

    def _pct(dias):
        for lo, hi, p in regua:
            if lo <= dias <= hi:
                return p
        return regua[-1][2]

    d["pct_pdd"] = d["dias_atraso"].apply(_pct)
    d["pdd"] = d["valor"] * d["pct_pdd"] / 100
    total_carteira = float(d["valor"].sum())
    total_pdd = float(d["pdd"].sum())
    faixas = pd.cut(d["dias_atraso"], bins=[-1, 30, 60, 90, 180, 10**9],
                    labels=["0–30", "31–60", "61–90", "91–180", "180+"])
    breakdown = d.groupby(faixas, observed=False)["valor"].sum()
    return dict(total_pdd=total_pdd, total_carteira=total_carteira,
               pct_pdd=(total_pdd / total_carteira if total_carteira else 0.0),
               breakdown=breakdown)


def enquadramento(cessoes_ativas: pd.DataFrame, criterios: dict,
                  pl_referencia: float | None = None) -> dict:
    if cessoes_ativas.empty:
        return dict(carteira_total=0.0, concentracao_top10=0.0,
                   maior_sacado_pct=0.0, maior_sacado_nome=None,
                   por_sacado=pd.Series(dtype=float), enquadrado=True,
                   alertas=[])
    total = float(cessoes_ativas["valor"].sum())
    base = pl_referencia if pl_referencia else total
    por_sacado = (cessoes_ativas.groupby("sacado")["valor"].sum()
                 .sort_values(ascending=False))
    top10_pct = float(por_sacado.head(10).sum() / base * 100)
    maior_pct = float(por_sacado.iloc[0] / base * 100)
    alertas = []
    if maior_pct > criterios["concentracao_max_sacado"]:
        alertas.append(
            f"Maior sacado ({por_sacado.index[0]}) responde por "
            f"{maior_pct:.1f}% do PL do fundo, acima do limite de "
            f"{criterios['concentracao_max_sacado']:.0f}%.")
    return dict(carteira_total=total, concentracao_top10=top10_pct,
               maior_sacado_pct=maior_pct,
               maior_sacado_nome=por_sacado.index[0], por_sacado=por_sacado,
               enquadrado=not alertas, alertas=alertas)


def alertas_cruzados(cessoes_por_deal: dict, deals_meta: dict,
                     limite_pct: float = 10.0) -> pd.DataFrame:
    """Sacados presentes em mais de um fundo do banco, com exposição
    consolidada — risco que nenhum enquadramento individual enxerga."""
    linhas = []
    for deal_id, df in cessoes_por_deal.items():
        if df.empty or deal_id not in deals_meta:
            continue
        for sacado, valor in df.groupby("sacado")["valor"].sum().items():
            linhas.append(dict(sacado=sacado, deal_id=deal_id,
                               fundo=deals_meta[deal_id]["nome_fundo"],
                               valor=valor))
    if not linhas:
        return pd.DataFrame(columns=["sacado", "valor_total", "n_fundos",
                                     "fundos"])
    d = pd.DataFrame(linhas)
    agg = d.groupby("sacado").agg(
        valor_total=("valor", "sum"), n_fundos=("deal_id", "nunique"),
        fundos=("fundo", lambda s: ", ".join(sorted(set(s)))))
    agg = agg[agg["n_fundos"] > 1].sort_values("valor_total", ascending=False)
    return agg.reset_index()
