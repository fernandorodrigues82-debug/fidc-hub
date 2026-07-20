import plotly.graph_objects as go
import streamlit as st

import db
from engine.conceitos import ajuda
from engine.waterfall import Estrutura, simular, stress_breakeven

st.set_page_config(page_title="Simulador de estruturação", page_icon="🧮",
                   layout="wide")
db.init_db()

st.title("Simulador de estruturação")
st.caption("Peça de decisão do comitê: cascata de pagamentos, stress de "
           "inadimplência e break-even da sênior — antes de o banco "
           "comprometer capital como cotista.")

with st.sidebar:
    st.caption("📖 Dúvida em algum parâmetro? Toque no (?) de cada campo ou "
               "abra a página **Guia de Conceitos** no menu.")
    st.header("Parâmetros da estrutura")
    pl = st.number_input("PL total (R$)", min_value=1e6, value=100e6, step=10e6,
                         format="%.0f", help=ajuda("pl_total"))
    pct_sen = st.slider("Cota sênior (% do PL)", 40, 90, 75, help=ajuda("senior")) / 100
    pct_mez = st.slider("Cota mezanino (% do PL)", 0, 30, 10, help=ajuda("mezanino")) / 100
    if pct_sen + pct_mez >= 1:
        st.error("Sênior + mezanino deve ser menor que 100%.")
        st.stop()
    st.metric("Subordinada (residual)", f"{(1-pct_sen-pct_mez)*100:.0f}%",
              help=ajuda("subordinada"))

    st.header("Taxas (a.m.)")
    t_sen = st.number_input("Alvo sênior (%)", value=1.10, step=0.05,
                            help=ajuda("taxa_senior")) / 100
    t_mez = st.number_input("Alvo mezanino (%)", value=1.40, step=0.05,
                            help=ajuda("taxa_mezanino")) / 100
    t_ces = st.number_input("Taxa de cessão da carteira (%)", value=2.20,
                            step=0.05, help=ajuda("taxa_cessao")) / 100

    st.header("Carteira e prazos")
    prazo = st.slider("Prazo médio dos recebíveis (meses)", 1, 12, 3,
                      help=ajuda("prazo_medio"))
    revolv = st.slider("Revolvência (meses)", 0, 60, 24, help=ajuda("revolvencia"))
    prep = st.number_input("Pré-pagamento (% a.m.)", value=1.0, step=0.5,
                           help=ajuda("prepagamento")) / 100

    st.header("Risco")
    inad = st.number_input("Inadimplência base (% dos vencimentos/mês)",
                           value=0.80, step=0.10, help=ajuda("inadimplencia")) / 100
    recup = st.slider("Recuperação de créditos vencidos (%)", 0, 80, 30,
                      help=ajuda("recuperacao")) / 100
    custos = st.number_input("Custos do fundo (% PL a.a.)", value=1.20,
                             step=0.10, help=ajuda("custos")) / 100
    stress = st.slider("Stress sobre a inadimplência (x)", 1.0, 15.0, 1.0,
                       step=0.5, help=ajuda("stress"))

e = Estrutura(pl_total=pl, pct_senior=pct_sen, pct_mezanino=pct_mez,
              taxa_senior_am=t_sen, taxa_mezanino_am=t_mez,
              taxa_cessao_am=t_ces, prazo_medio_meses=prazo,
              meses_revolvencia=revolv, inadimplencia_am=inad,
              prepagamento_am=prep, recuperacao=recup, custos_aa=custos,
              stress=stress)
r = simular(e)
res = r.resumo

# ------------------------------------------------------------------ métricas
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Sênior íntegra?", "Sim ✅" if res["senior_integra"] else "NÃO ⚠️",
          delta=None if res["senior_integra"]
          else f"-R$ {res['senior_shortfall']/1e6:,.1f} mi")
tir_s = res["tir_senior_aa"]
c2.metric("TIR sênior (a.a.)", f"{tir_s*100:.2f}%" if tir_s else "—",
          help=ajuda("tir"))
c3.metric("Perdas totais vs subordinada",
          f"{res['perdas_vs_sub']*100:.0f}%")
c4.metric("Retorno da sub (múltiplo)", f"{res['retorno_sub_multiplo']:.2f}x")
be = stress_breakeven(e)
c5.metric("Break-even sênior", f"{be}x inad. base" if be else "abaixo do base ⚠️",
          help=ajuda("stress"))

if not res["senior_integra"]:
    st.error("Neste cenário a cota sênior sofre perda — a estrutura não passa "
             "no comitê. Aumente a subordinação, reduza a revolvência ou "
             "melhore a taxa de cessão.")
elif be and be < 3:
    st.warning(f"Sênior íntegra, mas o colchão é curto: rompe a {be}x a "
               "inadimplência base. Comitês costumam exigir folga maior.")

# ------------------------------------------------------------------ gráficos
fluxo = r.fluxo
g1, g2 = st.columns(2)

with g1:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fluxo["mes"], y=fluxo["carteira"] / 1e6,
                             name="Carteira", fill="tozeroy"))
    fig.add_trace(go.Scatter(x=fluxo["mes"], y=fluxo["saldo_senior"] / 1e6,
                             name="Saldo sênior"))
    fig.add_trace(go.Scatter(x=fluxo["mes"], y=fluxo["saldo_mezanino"] / 1e6,
                             name="Saldo mezanino"))
    fig.add_vline(x=e.meses_revolvencia, line_dash="dot",
                  annotation_text="fim da revolvência")
    fig.update_layout(title="Carteira e saldos por classe (R$ mi)",
                      xaxis_title="Mês", height=380,
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")

with g2:
    fig2 = go.Figure()
    for col, nome in [("pago_senior", "Sênior"), ("pago_mezanino", "Mezanino"),
                      ("pago_sub", "Subordinada"), ("despesas", "Despesas")]:
        fig2.add_trace(go.Bar(x=fluxo["mes"], y=fluxo[col] / 1e6, name=nome))
    fig2.update_layout(barmode="stack",
                       title="Cascata: pagamentos mensais (R$ mi)",
                       xaxis_title="Mês", height=380,
                       legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig2, width="stretch")

fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=fluxo["mes"], y=fluxo["perdas_acum"] / 1e6,
                          name="Perdas acumuladas", fill="tozeroy",
                          line=dict(color="#B33A3A")))
fig3.add_hline(y=e.pl_total * e.pct_sub / 1e6, line_dash="dash",
               annotation_text="Subordinada inicial")
fig3.update_layout(title="Perdas acumuladas vs colchão de subordinação (R$ mi)",
                   xaxis_title="Mês", height=300)
st.plotly_chart(fig3, width="stretch")

with st.expander("Fluxo mês a mês (dados da simulação)"):
    st.dataframe(fluxo, hide_index=True, width="stretch")

# --------------------------------------------------- aprovar e virar um deal
st.divider()
st.subheader("Aprovar estrutura e abrir esteira de constituição")
origs = [o for o in db.listar_originadores()]
if origs:
    ca, cb, cc = st.columns([2, 2, 1])
    orig_sel = ca.selectbox("Originador", [o["id"] for o in origs],
                            format_func=lambda i: next(
                                o["razao_social"] for o in origs
                                if o["id"] == i))
    nome_fundo = cb.text_input("Nome do fundo",
                               value="FIDC " + next(
                                   o["razao_social"].split()[0]
                                   for o in origs if o["id"] == orig_sel))
    if cc.button("Aprovar →", width="stretch",
                 disabled=not res["senior_integra"]):
        deal_id = db.criar_deal(orig_sel, nome_fundo,
                                {**e.__dict__, "resumo": res})
        db.mover_etapa(orig_sel, "Aprovado")
        st.success(f"Deal #{deal_id} criado com os parâmetros desta simulação. "
                   "Acompanhe na Esteira de Constituição.")
    if not res["senior_integra"]:
        st.caption("A aprovação fica bloqueada enquanto a sênior sofrer perda "
                   "no cenário simulado.")
else:
    st.info("Cadastre um originador no funil para vincular a estrutura.")
