import plotly.graph_objects as go
import streamlit as st

import db
from engine.conceitos import ajuda
from engine.parser_operacao import interpretar_llm, interpretar_local
from engine.waterfall import Estrutura, simular, stress_breakeven

st.set_page_config(page_title="Simulador de estruturação", page_icon="🧮",
                   layout="wide")
db.init_db()


def _cdi_para_am(cdi_aa: float, spread_aa: float) -> float:
    """CDI + spread (% a.a.) → taxa mensal equivalente."""
    return (1 + (cdi_aa + spread_aa) / 100) ** (1 / 12) - 1


# ---------------------------------------------------------------- defaults
DEFAULTS = dict(pl=100e6, pct_sen=75, pct_mez=10, modo_taxa="CDI + spread (a.a.)",
                cdi_aa=12.0, spread_sen=3.0, spread_mez=6.0,
                t_sen_am=1.10, t_mez_am=1.40, t_ces=2.20, prazo=3, revolv=24,
                prep=1.0, inad=0.80, recup=30, custos=1.20, stress=1.0)
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

# aplica parâmetros vindos do interpretador ANTES de criar os widgets
if pend := st.session_state.pop("_aplicar", None):
    mapa = {"pl_total": ("pl", lambda v: float(v)),
            "pct_senior": ("pct_sen", lambda v: int(round(v * 100))),
            "pct_mezanino": ("pct_mez", lambda v: int(round(v * 100))),
            "cdi_aa": ("cdi_aa", float),
            "spread_senior": ("spread_sen", float),
            "spread_mezanino": ("spread_mez", float),
            "taxa_cessao_am": ("t_ces", lambda v: round(v * 100, 2)),
            "prazo_medio_meses": ("prazo", int),
            "meses_revolvencia": ("revolv", int),
            "inadimplencia_am_pct": ("inad", float)}
    for origem, (chave, conv) in mapa.items():
        if origem in pend:
            st.session_state[chave] = conv(pend[origem])
    if "spread_senior" in pend or "spread_mezanino" in pend:
        st.session_state["modo_taxa"] = "CDI + spread (a.a.)"

st.title("Simulador de estruturação")
st.caption("Peça de decisão do comitê: cascata de pagamentos, stress de "
           "inadimplência e break-even da sênior — antes de o banco "
           "comprometer capital como cotista.")

# ------------------------------------------------- descrever a operação
with st.expander("✍️ Descrever a operação (a IA preenche os parâmetros)",
                 expanded=False):
    st.caption("Escreva como você falaria com a mesa: "
               "*\"FIDC de duplicatas de 80 milhões, sênior de 70% a CDI+3,5, "
               "mezanino de 10% a CDI+6, cessão de 2,4% a.m., prazo médio de "
               "60 dias, revolvência de 2 anos, perda histórica de 1,2%\"*")
    texto = st.text_area("Descrição da operação", height=110,
                         label_visibility="collapsed")
    c_a, c_b = st.columns([1, 3])
    if c_a.button("Interpretar e preencher", type="primary",
                  use_container_width=True):
        if not texto.strip():
            st.warning("Escreva a descrição primeiro.")
        else:
            try:
                api_key = st.secrets.get("ANTHROPIC_API_KEY", None)
            except Exception:
                api_key = None
            try:
                if api_key:
                    params, log = interpretar_llm(texto, api_key)
                    fonte = "IA (API Anthropic)"
                else:
                    params, log = interpretar_local(texto)
                    fonte = "interpretação local"
            except Exception:
                params, log = interpretar_local(texto)
                fonte = "interpretação local (API indisponível)"
            if params:
                st.session_state["_aplicar"] = params
                st.session_state["_log_parser"] = (fonte, log)
                st.rerun()
            else:
                st.info("Não identifiquei parâmetros. Tente citar valores "
                        "como no exemplo acima.")
    c_b.caption("🎤 Descrição por voz: use o microfone do teclado do celular "
                "nesta caixa. Transcrição de áudio nativa entra na próxima "
                "fase (requer chave de API em *Settings → Secrets*).")

if lg := st.session_state.pop("_log_parser", None):
    fonte, itens = lg
    st.success(f"Parâmetros aplicados via {fonte}: " + " · ".join(itens) +
               ". Revise nos controles ao lado antes de aprovar.")

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.caption("📖 Dúvida em algum parâmetro? Toque no (?) de cada campo ou "
               "abra a página **Guia de Conceitos** no menu.")

    with st.expander("💰 Estrutura de capital", expanded=True):
        pl = st.number_input("PL total (R$)", min_value=1e6, step=10e6,
                             format="%.0f", key="pl", help=ajuda("pl_total"))
        pct_sen = st.slider("Cota sênior (% do PL)", 40, 90, key="pct_sen",
                            help=ajuda("senior")) / 100
        pct_mez = st.slider("Cota mezanino (% do PL)", 0, 30, key="pct_mez",
                            help=ajuda("mezanino")) / 100
        if pct_sen + pct_mez >= 1:
            st.error("Sênior + mezanino deve ser menor que 100%.")
            st.stop()
        st.metric("Subordinada (residual)",
                  f"{(1 - pct_sen - pct_mez) * 100:.0f}%",
                  help=ajuda("subordinada"))

    with st.expander("📈 Taxas", expanded=True):
        modo = st.radio("Como informar as taxas das cotas?",
                        ["CDI + spread (a.a.)", "Taxa fixa (a.m.)"],
                        key="modo_taxa", horizontal=True)
        if modo == "CDI + spread (a.a.)":
            cdi = st.number_input("CDI projetado (% a.a.)", min_value=0.0,
                                  step=0.25, key="cdi_aa",
                                  help="Cenário de CDI usado para converter "
                                       "os spreads em taxa mensal do modelo.")
            sp_s = st.number_input("Sênior: CDI + (% a.a.)", step=0.25,
                                   key="spread_sen", help=ajuda("taxa_senior"))
            sp_m = st.number_input("Mezanino: CDI + (% a.a.)", step=0.25,
                                   key="spread_mez", help=ajuda("taxa_mezanino"))
            t_sen = _cdi_para_am(cdi, sp_s)
            t_mez = _cdi_para_am(cdi, sp_m)
            st.caption(f"Equivalente mensal → sênior {t_sen*100:.2f}% · "
                       f"mezanino {t_mez*100:.2f}%")
        else:
            t_sen = st.number_input("Alvo sênior (% a.m.)", step=0.05,
                                    key="t_sen_am",
                                    help=ajuda("taxa_senior")) / 100
            t_mez = st.number_input("Alvo mezanino (% a.m.)", step=0.05,
                                    key="t_mez_am",
                                    help=ajuda("taxa_mezanino")) / 100
        t_ces = st.number_input("Taxa de cessão da carteira (% a.m.)",
                                step=0.05, key="t_ces",
                                help=ajuda("taxa_cessao")) / 100

    with st.expander("📅 Carteira e prazos", expanded=False):
        prazo = st.slider("Prazo médio dos recebíveis (meses)", 1, 12,
                          key="prazo", help=ajuda("prazo_medio"))
        revolv = st.slider("Revolvência (meses)", 0, 60, key="revolv",
                           help=ajuda("revolvencia"))
        prep = st.number_input("Pré-pagamento (% a.m.)", step=0.5, key="prep",
                               help=ajuda("prepagamento")) / 100

    with st.expander("⚠️ Risco", expanded=False):
        inad = st.number_input("Inadimplência base (% dos vencimentos/mês)",
                               step=0.10, key="inad",
                               help=ajuda("inadimplencia")) / 100
        recup = st.slider("Recuperação de créditos vencidos (%)", 0, 80,
                          key="recup", help=ajuda("recuperacao")) / 100
        custos = st.number_input("Custos do fundo (% PL a.a.)", step=0.10,
                                 key="custos", help=ajuda("custos")) / 100
        stress = st.slider("Stress sobre a inadimplência (x)", 1.0, 15.0,
                           step=0.5, key="stress", help=ajuda("stress"))

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
c3.metric("Perdas totais vs subordinada", f"{res['perdas_vs_sub']*100:.0f}%")
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
