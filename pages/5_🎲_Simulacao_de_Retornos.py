import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.figure_factory as ff
import streamlit as st

import db
from engine.conceitos import ajuda
from engine.rating import classificar_estrutura
from engine.waterfall import Classe, Estrutura, montecarlo

st.set_page_config(page_title="Simulação de retornos", page_icon="🎲",
                   layout="wide")
db.init_db()

st.title("Simulação de retornos das cotas")
st.caption("Monte Carlo sobre a mesma estrutura do simulador: em vez de um "
           "número por cenário, a distribuição de retorno de cada classe — "
           "com probabilidade de perda. Parte da última estrutura montada "
           "no Simulador de Estruturação; ajuste aqui se quiser.")

if "estrutura_atual" not in st.session_state:
    st.info("Nenhuma estrutura carregada ainda. Abra o **Simulador de "
            "Estruturação**, monte as classes e volte aqui — ou use os "
            "valores de exemplo abaixo.")
    base = Estrutura()
else:
    base = st.session_state["estrutura_atual"]

with st.expander("Estrutura em uso", expanded=False):
    for c in base.classes:
        tag = " (residual)" if c.residual else f" — taxa-alvo {c.taxa_am*100:.2f}% a.m."
        st.write(f"**{c.nome}** — {c.pct*100:.1f}% do PL{tag}")
    st.caption(f"PL R$ {base.pl_total/1e6:,.0f} mi · cessão "
              f"{base.taxa_cessao_am*100:.2f}% a.m. · inadimplência base "
              f"{base.inadimplencia_am*100:.2f}% a.m. · stress "
              f"{base.stress:.1f}x · revolvência {base.meses_revolvencia} m"
              + (f" · gatilho sub. mín. {base.sub_minima*100:.0f}%"
                 if base.sub_minima else ""))

st.subheader("Calibração de volatilidade e persistência")
origs = db.listar_originadores()
calib = None
orig_sel = None
if origs:
    default_idx = 0
    ids = [o["id"] for o in origs]
    if st.session_state.get("ultimo_originador_id") in ids:
        default_idx = ids.index(st.session_state["ultimo_originador_id"])
    orig_sel = st.selectbox(
        "Originador (para usar a calibração real da carteira, se houver)",
        ids, index=default_idx,
        format_func=lambda i: next(o["razao_social"] for o in origs
                                   if o["id"] == i))
    calib = db.obter_calibracao(orig_sel)

usar_calibracao = False
if calib:
    st.success(
        f"📊 Calibração real disponível: taxa média observada "
        f"{calib['taxa_media_am']*100:.2f}%/mês, volatilidade "
        f"{calib['vol']}, persistência {calib['rho']} — extraída de "
        f"{calib['n_meses']} meses ({calib['periodo']}).")
    usar_calibracao = st.checkbox(
        "Usar a volatilidade e persistência calibradas da carteira real",
        value=True)
    st.caption(
        "⚠️ A calibração define a **forma** da distribuição (quanto oscila "
        "e quanto o ciclo persiste) a partir do aging observado da "
        "carteira — não é uma perda líquida definitiva por safra madura. "
        "O **nível** da inadimplência base continua sendo o parâmetro "
        "'Inadimplência base' do Simulador de Estruturação, validado na "
        "due diligence — a calibração aqui não o substitui.")
else:
    st.caption("Nenhuma calibração de carteira encontrada para este "
              "originador. Suba a carteira dele em **Funil de Originadores "
              "→ Análise de carteira** para calibrar automaticamente com "
              "dados reais — por ora, ajuste manualmente abaixo.")

c1, c2, c3 = st.columns(3)
n_sims = c1.select_slider("Nº de cenários", [200, 500, 1000, 2000], value=500,
                          help="Mais cenários = distribuição mais precisa, "
                               "porém mais lento.")
if usar_calibracao and calib:
    vol = calib["vol"]
    rho = calib["rho"]
    c2.metric("Volatilidade (calibrada)", vol)
    c3.metric("Persistência ρ (calibrada)", rho)
else:
    vol = c2.slider("Volatilidade da inadimplência", 0.2, 1.5, 0.6, step=0.1,
                    help="Dispersão mês a mês em torno da inadimplência "
                         "base. 0,3 ≈ carteira estável (ex.: consignado). "
                         "0,8–1,2 ≈ carteira volátil (ex.: PMEs, alta "
                         "concentração).")
    rho = c3.slider("Persistência do ciclo (autocorrelação)", 0.0, 0.9, 0.6,
                    step=0.1,
                    help="Quanto meses ruins tendem a ser seguidos por "
                         "meses ruins — captura o ciclo de crédito em vez "
                         "de choques isolados e independentes.")

if st.button("🎲 Rodar simulação de retornos", type="primary",
            use_container_width=True):
    t0 = time.time()
    with st.spinner(f"Simulando {n_sims} cenários de ciclo de crédito..."):
        df, stats = montecarlo(base, n_sims=n_sims, vol=vol, rho=rho)
    st.session_state["mc_resultado"] = (df, stats, time.time() - t0)
    st.session_state["mc_fingerprint"] = (
        round(base.pl_total), tuple((c.nome, round(c.pct, 4),
                                     round(c.taxa_am, 6))
                                    for c in base.classes),
        round(base.taxa_cessao_am, 6), base.prazo_medio_meses,
        base.meses_revolvencia, base.meses_rampa, base.meses_carencia,
        round(base.inadimplencia_am, 6),
        round(base.stress, 2))

if "mc_resultado" not in st.session_state:
    st.stop()

df, stats, dt = st.session_state["mc_resultado"]
st.caption(f"{len(df['sim'].unique())} cenários simulados em {dt:.1f}s.")

# ------------------------------------------------------------------ resumo
st.subheader("Retorno esperado por classe")
with st.spinner("Calculando nota interna por classe..."):
    ratings = classificar_estrutura(base, stats)
tabela = stats.merge(ratings[["classe", "nota"]], on="classe", how="left")
for col in ["tir_media", "tir_p5", "tir_p50", "tir_p95"]:
    tabela[col] = tabela[col].map(
        lambda v: f"{v*100:.2f}%" if pd.notna(v) else "—")
tabela["prob_nao_integral"] = (stats["prob_nao_integral"] * 100).map(
    "{:.1f}%".format)
tabela["prob_perda_principal"] = (stats["prob_perda_principal"] * 100).map(
    "{:.1f}%".format)
tabela = tabela.rename(columns={"nota": "Nota interna"})
tabela.columns = ["Classe", "TIR média", "TIR p5 (pior 5%)", "TIR mediana",
                  "TIR p95 (melhor 5%)", "Prob. não receber 100%",
                  "Prob. perder principal", "Nota interna"]
tabela = tabela[["Classe", "Nota interna", "TIR média", "TIR p5 (pior 5%)",
                 "TIR mediana", "TIR p95 (melhor 5%)",
                 "Prob. não receber 100%", "Prob. perder principal"]]
st.dataframe(tabela, hide_index=True, width="stretch")
st.caption("TIR p5 = pior cenário entre os 5% mais adversos simulados — "
           "referência de 'quanto posso perder' para o comitê.")

# ------------------------------------------------------------ distribuição
st.subheader("Distribuição de retorno por classe")
classes_nomes = list(stats["classe"])
sel_classes = st.multiselect("Classes a exibir", classes_nomes,
                             default=classes_nomes)

dados_hist, labels = [], []
for c in sel_classes:
    vals = pd.to_numeric(df.loc[df["classe"] == c, "tir_aa"],
                         errors="coerce").dropna() * 100
    if len(vals) > 5 and vals.std() > 1e-6:
        dados_hist.append(vals.values)
        labels.append(c)

if dados_hist:
    fig = ff.create_distplot(dados_hist, labels, show_hist=False,
                             show_rug=False)
    fig.update_layout(title="Densidade da TIR anualizada por classe (%)",
                      xaxis_title="TIR (% a.a.)", height=380,
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")
else:
    st.info("As classes selecionadas tiveram retorno praticamente constante "
            "nos cenários simulados (não sofreram no stress aplicado) — "
            "não há o que plotar como distribuição. Aumente a volatilidade "
            "ou o stress da estrutura para testar o limite delas.")

# ------------------------------------------------------------- box por classe
fig_box = go.Figure()
for c in classes_nomes:
    vals = pd.to_numeric(df.loc[df["classe"] == c, "tir_aa"],
                         errors="coerce").dropna() * 100
    fig_box.add_trace(go.Box(y=vals, name=c, boxmean=True))
fig_box.update_layout(title="Faixa de retorno por classe (box plot, % a.a.)",
                      yaxis_title="TIR (% a.a.)", height=380)
st.plotly_chart(fig_box, width="stretch")

st.divider()
st.caption(
    "Metodologia: a inadimplência mensal segue um processo estocástico com "
    "persistência (AR(1)) em torno da inadimplência base × stress definidos "
    "no Simulador — meses ruins tendem a vir em sequência, como em um ciclo "
    "de crédito real, em vez de choques independentes mês a mês. Quando "
    "calibrada por carteira real, a volatilidade e a persistência vêm do "
    "aging observado por safra de vencimento (proxy, não perda líquida "
    "definitiva); o nível da inadimplência base continua sendo o parâmetro "
    "validado na due diligence. Ferramenta de apoio à decisão; não é "
    "precificação de mercado nem garantia de retorno.")
