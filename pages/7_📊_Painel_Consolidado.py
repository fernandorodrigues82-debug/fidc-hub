import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
from engine.operacao import alertas_cruzados, enquadramento
from engine.rating import classificar_estrutura
from engine.waterfall import estrutura_de_params

st.set_page_config(page_title="Painel consolidado", page_icon="📊",
                   layout="wide")
db.init_db()

st.title("Painel consolidado")
st.caption("Todos os fundos do banco lado a lado: risco-retorno, quem está "
           "mais perto do limite e a concentração de sacados agregada "
           "entre fundos.")

deals = db.listar_deals()
if not deals:
    st.info("Nenhum fundo aprovado ainda.")
    st.stop()

# ------------------------------------------------------- monta a tabela geral
linhas = []
for d in deals:
    params = json.loads(d["parametros"])
    try:
        e = estrutura_de_params(params)
    except Exception:
        continue
    sub_pct = next((c["pct"] for c in params.get("classes", [])
                    if c.get("residual")), None)
    senior_nome = e.classes[0].nome
    with st.spinner(f"Avaliando {d['nome_fundo']}..."):
        rt = classificar_estrutura(e)
    nota_senior = rt.iloc[0]["nota"] if len(rt) else "—"
    be_senior = rt.iloc[0].get("breakeven") if len(rt) else None

    ativa = db.carteira_ativa(d["id"])
    criterios = db.obter_criterios(d["id"])
    enq = enquadramento(ativa, criterios,
                        pl_referencia=params.get("pl_total", 0.0))

    linhas.append(dict(
        deal_id=d["id"], fundo=d["nome_fundo"],
        originador=d.get("razao_social") or "—", status=d["status"],
        pl=params.get("pl_total", 0), subordinacao=sub_pct,
        classe_senior=senior_nome, nota_senior=nota_senior,
        breakeven_senior=be_senior,
        tir_sub=params.get("resumo", {}).get("retorno_sub_multiplo"),
        carteira_ativa=enq["carteira_total"],
        enquadrado=enq["enquadrado"] if enq["carteira_total"] > 0 else None,
        maior_sacado_pct=enq["maior_sacado_pct"]))

painel = pd.DataFrame(linhas)
if painel.empty:
    st.warning("Não foi possível reconstruir os parâmetros salvos destes "
              "fundos (deals antigos, de antes desta versão). Aprove "
              "novas estruturas para vê-las aqui.")
    st.stop()

# --------------------------------------------------------------- destaques
desenquadrados = painel[painel["enquadrado"] == False]  # noqa: E712
frageis = painel[painel["breakeven_senior"].fillna(99) < 3]
if len(desenquadrados) or len(frageis):
    st.error(
        "⚠️ Atenção: " +
        (f"{len(desenquadrados)} fundo(s) desenquadrado(s) "
         f"({', '.join(desenquadrados['fundo'])}). "
         if len(desenquadrados) else "") +
        (f"{len(frageis)} fundo(s) com colchão de sênior curto "
         f"(< 3x, {', '.join(frageis['fundo'])})."
         if len(frageis) else ""))
else:
    st.success("✅ Nenhum fundo desenquadrado ou com colchão de sênior "
              "abaixo de 3x no momento.")

# ------------------------------------------------------------------ métricas
c1, c2, c3, c4 = st.columns(4)
c1.metric("Fundos", len(painel))
c2.metric("PL total (R$ mi)", f"{painel['pl'].sum()/1e6:,.0f}")
c3.metric("Carteira ativa total (R$ mi)",
          f"{painel['carteira_ativa'].sum()/1e6:,.1f}")
c4.metric("Notas AAA/AA na sênior",
          f"{painel['nota_senior'].isin(['AAA', 'AA']).sum()}/{len(painel)}")

# --------------------------------------------------------------- tabela geral
st.subheader("Fundos do banco")
tv = painel.copy()
tv["PL (R$ mi)"] = (tv["pl"] / 1e6).round(1)
tv["Subordinação"] = (tv["subordinacao"] * 100).round(1).astype(str) + "%"
tv["Carteira ativa (R$ mi)"] = (tv["carteira_ativa"] / 1e6).round(2)
tv["Enquadramento"] = tv["enquadrado"].map(
    {True: "✅", False: "⚠️", None: "— sem carteira ativa"})
tv["Break-even sênior"] = tv["breakeven_senior"].map(
    lambda v: f"{v:.1f}x" if pd.notna(v) else "—")
tv["Retorno sub (múltiplo)"] = tv["tir_sub"].map(
    lambda v: f"{v:.2f}x" if pd.notna(v) else "—")
st.dataframe(
    tv[["fundo", "originador", "status", "PL (R$ mi)", "Subordinação",
       "nota_senior", "Break-even sênior", "Retorno sub (múltiplo)",
       "Carteira ativa (R$ mi)", "Enquadramento"]]
    .rename(columns={"fundo": "Fundo", "originador": "Originador",
                     "status": "Status", "nota_senior": "Nota sênior"}),
    hide_index=True, width="stretch")

# --------------------------------------------------------- risco-retorno
st.subheader("Risco × retorno")
cores = {"AAA": "#1C6B2E", "AA": "#4C9A4C", "A": "#8AB84C", "BBB": "#D6B33A",
        "BB": "#E08A3A", "B": "#C9553A", "CCC": "#B33A3A", "NR": "#999999"}
fig = go.Figure()
for nota, g in painel.groupby(painel["nota_senior"].fillna("—")):
    fig.add_trace(go.Scatter(
        x=g["breakeven_senior"], y=g["tir_sub"], mode="markers+text",
        text=g["fundo"], textposition="top center", name=nota,
        marker=dict(size=(g["pl"] / painel["pl"].max() * 40 + 10),
                   color=cores.get(nota, "#999999")),
        hovertemplate="%{text}<br>break-even: %{x:.1f}x<br>"
                     "retorno sub: %{y:.2f}x<extra></extra>"))
fig.update_layout(
    title="Break-even da sênior × retorno da júnior "
          "(tamanho da bolha = PL)",
    xaxis_title="Break-even sênior (x inadimplência base)",
    yaxis_title="Retorno da júnior (múltiplo do aporte)",
    height=420, legend_title="Nota sênior")
st.plotly_chart(fig, width="stretch")
st.caption("Quadrante ideal: canto superior direito — colchão largo na "
          "sênior e bom retorno para quem sobe o risco na júnior. "
          "Fundos à esquerda merecem atenção do comitê de risco.")

# ------------------------------------------------------ concentração cruzada
st.subheader("Concentração de sacados entre fundos")
todos_ativos = db.carteira_ativa_todos_deals()
deals_meta = {d["id"]: d for d in deals}
ac = alertas_cruzados(todos_ativos, deals_meta)
if ac.empty:
    st.info("Nenhum sacado repetido entre fundos com carteira ativa, até "
            "o momento.")
else:
    ac_view = ac.copy()
    ac_view["valor_total"] = (ac_view["valor_total"] / 1e6).round(2)
    ac_view.columns = ["Sacado", "Exposição total (R$ mi)", "Nº fundos",
                       "Fundos"]
    st.dataframe(ac_view, hide_index=True, width="stretch")
    st.caption("Mesmo sacado presente em mais de um fundo do banco — vale "
              "revisar a exposição consolidada frente ao apetite de risco "
              "do banco como um todo.")
