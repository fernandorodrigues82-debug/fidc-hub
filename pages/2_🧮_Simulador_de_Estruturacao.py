import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
from engine.conceitos import ajuda
from engine.parser_operacao import interpretar_llm, interpretar_local
from engine.memorando import gerar_memorando
from engine.rating import classificar_estrutura
from engine.sensibilidade import tornado
from engine.waterfall import (Classe, Estrutura, analise_suporte,
                              curva_stress, simular, stress_breakeven)

st.set_page_config(page_title="Simulador de estruturação", page_icon="🧮",
                   layout="wide")
db.init_db()

BENCHMARKS = ["CDI + spread (a.a.)", "% do CDI", "Prefixado (a.a.)",
              "Fixa (a.m.)"]


def _taxa_am(benchmark: str, valor: float, cdi_aa: float) -> float:
    cdi_am = (1 + cdi_aa / 100) ** (1 / 12) - 1
    if benchmark == "CDI + spread (a.a.)":
        return (1 + (cdi_aa + valor) / 100) ** (1 / 12) - 1
    if benchmark == "% do CDI":
        return cdi_am * valor / 100
    if benchmark == "Prefixado (a.a.)":
        return (1 + valor / 100) ** (1 / 12) - 1
    return valor / 100  # Fixa (a.m.)


CLASSES_PADRAO = pd.DataFrame([
    {"Classe": "Sênior", "% do PL": 75.0,
     "Benchmark": "CDI + spread (a.a.)", "Valor": 3.0},
    {"Classe": "Mezanino", "% do PL": 10.0,
     "Benchmark": "CDI + spread (a.a.)", "Valor": 6.0},
])

DEFAULTS = dict(pl=100e6, sub_min=12.0, cdi_aa=12.0, t_ces=2.20, prazo=3, revolv=24,
                prep=1.0, inad=0.80, recup=30, custos=1.20, stress=1.0)
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
if "classes_df" not in st.session_state:
    st.session_state["classes_df"] = CLASSES_PADRAO.copy()

# aplica parâmetros vindos do interpretador ANTES de criar os widgets
if pend := st.session_state.pop("_aplicar", None):
    simples = {"pl_total": ("pl", float), "cdi_aa": ("cdi_aa", float),
               "taxa_cessao_am": ("t_ces", lambda v: round(v * 100, 2)),
               "prazo_medio_meses": ("prazo", int),
               "meses_revolvencia": ("revolv", int),
               "inadimplencia_am_pct": ("inad", float)}
    for origem, (chave, conv) in simples.items():
        if origem in pend:
            st.session_state[chave] = conv(pend[origem])
    cdf = st.session_state["classes_df"].copy()

    def _linha(nome_parcial):
        hits = cdf.index[cdf["Classe"].str.lower()
                         .str.contains(nome_parcial, na=False)]
        return hits[0] if len(hits) else None

    for chave_pct, chave_sp, alvo in [("pct_senior", "spread_senior", "nior"),
                                      ("pct_mezanino", "spread_mezanino",
                                       "mez")]:
        i = _linha(alvo)
        if i is None:
            continue
        if chave_pct in pend:
            cdf.at[i, "% do PL"] = round(pend[chave_pct] * 100, 1)
        if chave_sp in pend:
            cdf.at[i, "Benchmark"] = "CDI + spread (a.a.)"
            cdf.at[i, "Valor"] = float(pend[chave_sp])
    st.session_state["classes_df"] = cdf

st.title("Simulador de estruturação")
st.caption("Peça de decisão do comitê: estrutura multiclasses com pagamento "
           "sequencial por senioridade, stress de inadimplência e break-even "
           "— antes de o banco comprometer capital como cotista.")

# ------------------------------------------------- descrever a operação
with st.expander("✍️ Descrever a operação (a IA preenche os parâmetros)"):
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
    c_b.caption("🎤 Por voz: use o microfone do teclado do celular nesta "
                "caixa. Transcrição nativa de áudio entra na próxima fase "
                "(requer chave de API em *Settings → Secrets*).")

if lg := st.session_state.pop("_log_parser", None):
    fonte, itens = lg
    st.success(f"Parâmetros aplicados via {fonte}: " + " · ".join(itens) +
               ". Revise antes de aprovar.")

# ---------------------------------------------------------- classes de cotas
st.subheader("Classes de cotas")
st.caption("Ordem = senioridade (1ª linha recebe primeiro). Adicione ou "
           "remova linhas para séries e classes extras — ex.: Sênior A, "
           "Sênior B, Mezanino. A subordinada é o residual, calculado "
           "automaticamente.")
cdf = st.data_editor(
    st.session_state["classes_df"], num_rows="dynamic", width="stretch",
    key="editor_classes",
    column_config={
        "Classe": st.column_config.TextColumn(required=True),
        "% do PL": st.column_config.NumberColumn(min_value=1.0,
                                                 max_value=95.0, step=1.0,
                                                 format="%.1f%%"),
        "Benchmark": st.column_config.SelectboxColumn(options=BENCHMARKS,
                                                      required=True),
        "Valor": st.column_config.NumberColumn(
            step=0.25, help="Spread (a.a.), % do CDI, taxa prefixada (a.a.) "
                            "ou taxa fixa (a.m.), conforme o benchmark."),
    })
cdf = cdf.dropna(subset=["Classe"]).reset_index(drop=True)
soma_pct = float(cdf["% do PL"].fillna(0).sum())
pct_sub = round(100 - soma_pct, 2)
m_sub1, m_sub2 = st.columns([1, 3])
m_sub1.metric("Subordinada (residual)", f"{pct_sub:.1f}%",
              help=ajuda("subordinada"))
if pct_sub <= 0:
    m_sub2.error("As classes somam ≥ 100% do PL — sobra nada para a "
                 "subordinada. Reduza os percentuais.")
    st.stop()
if len(cdf) == 0:
    st.error("Inclua ao menos uma classe além da subordinada.")
    st.stop()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.caption("📖 Dúvida em algum parâmetro? Toque no (?) de cada campo ou "
               "abra a página **Guia de Conceitos** no menu.")
    with st.expander("💰 Fundo e cenário", expanded=True):
        pl = st.number_input("PL total (R$)", min_value=1e6, step=10e6,
                             format="%.0f", key="pl", help=ajuda("pl_total"))
        cdi = st.number_input("CDI projetado (% a.a.)", min_value=0.0,
                              step=0.25, key="cdi_aa",
                              help="Usado nos benchmarks CDI+ e % do CDI.")
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
        sub_min_pct = st.number_input(
            "Subordinação mínima — gatilho (%)", min_value=0.0,
            max_value=60.0, step=1.0, key="sub_min",
            help="Evento de avaliação: se o índice de subordinação "
                 "dinâmico — (ativos − dívida das classes) / ativos — cair "
                 "abaixo deste mínimo durante a revolvência, o fundo para "
                 "de reinvestir e amortiza antecipadamente, protegendo as "
                 "classes por senioridade. 0 = sem gatilho.")

classes = [Classe(str(row["Classe"]), float(row["% do PL"]) / 100,
                  _taxa_am(row["Benchmark"], float(row["Valor"] or 0), cdi))
           for _, row in cdf.iterrows()]
classes.append(Classe("Subordinada", pct_sub / 100, 0.0, residual=True))

e = Estrutura(pl_total=pl, classes=classes, taxa_cessao_am=t_ces,
              prazo_medio_meses=prazo, meses_revolvencia=revolv,
              inadimplencia_am=inad, prepagamento_am=prep, recuperacao=recup,
              custos_aa=custos, stress=stress,
              sub_minima=(sub_min_pct / 100) if sub_min_pct > 0 else None)
r = simular(e)
res = r.resumo
pc = r.por_classe
st.session_state["estrutura_atual"] = e

# ------------------------------------------------------------------ métricas
c1, c2, c3, c4 = st.columns(4)
c1.metric("Todas as classes íntegras?",
          "Sim ✅" if res["todas_integras"] else "NÃO ⚠️")
c2.metric("Perdas totais vs subordinada", f"{res['perdas_vs_sub']*100:.0f}%")
c3.metric("Retorno da sub (múltiplo)", f"{res['retorno_sub_multiplo']:.2f}x")
be = stress_breakeven(e, 0)
c4.metric(f"Break-even {classes[0].nome}",
          f"{be}x inad. base" if be else "abaixo do base ⚠️",
          help=ajuda("stress"))

with st.spinner("Calculando rating interno por classe..."):
    ratings = classificar_estrutura(e)
tabela = pc.merge(ratings[["classe", "nota"]], on="classe", how="left")
tabela["TIR (a.a.)"] = tabela["tir_aa"].map(
    lambda v: f"{v*100:.2f}%" if v is not None and not pd.isna(v) else "—")
tabela["% do PL"] = (tabela["pct"] * 100).map("{:.1f}%".format)
tabela["Aporte (R$ mi)"] = (tabela["aporte"] / 1e6).round(1)
tabela["Recebido (R$ mi)"] = (tabela["recebido"] / 1e6).round(1)
tabela["Íntegra"] = tabela["integra"].map({True: "✅", False: "⚠️"})
tabela = tabela.rename(columns={"nota": "Nota interna"})
st.dataframe(tabela[["classe", "% do PL", "Aporte (R$ mi)",
                     "Recebido (R$ mi)", "TIR (a.a.)", "Nota interna",
                     "Íntegra"]],
             hide_index=True, width="stretch")
st.caption("Nota interna: régua própria do banco (não é rating de "
          "agência), combinando o break-even de stress de cada classe "
          "com a probabilidade do Monte Carlo quando disponível. Ver "
          "detalhe na página 'Simulação de Retornos'.")

if not res["todas_integras"]:
    piores = pc[~pc["integra"]]["classe"].tolist()
    st.error(f"Classe(s) com perda neste cenário: {', '.join(piores)}. "
             "Aumente a subordinação, reduza a revolvência ou melhore a "
             "taxa de cessão.")
elif be and be < 3:
    st.warning(f"{classes[0].nome} íntegra, mas o colchão é curto: rompe a "
               f"{be}x a inadimplência base.")

# ------------------------------------------------------------------ gráficos
fluxo = r.fluxo
g1, g2 = st.columns(2)
with g1:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fluxo["mes"], y=fluxo["carteira"] / 1e6,
                             name="Carteira", fill="tozeroy"))
    for c in classes[:-1]:
        fig.add_trace(go.Scatter(x=fluxo["mes"],
                                 y=fluxo[f"saldo_{c.nome}"] / 1e6,
                                 name=f"Saldo {c.nome}"))
    fig.add_vline(x=e.meses_revolvencia, line_dash="dot",
                  annotation_text="fim da revolvência")
    fig.update_layout(title="Carteira e saldos por classe (R$ mi)",
                      xaxis_title="Mês", height=380,
                      legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig, width="stretch")
with g2:
    fig2 = go.Figure()
    for c in classes[:-1]:
        fig2.add_trace(go.Bar(x=fluxo["mes"], y=fluxo[f"pago_{c.nome}"] / 1e6,
                              name=c.nome))
    fig2.add_trace(go.Bar(x=fluxo["mes"], y=fluxo["pago_residual"] / 1e6,
                          name="Subordinada"))
    fig2.add_trace(go.Bar(x=fluxo["mes"], y=fluxo["despesas"] / 1e6,
                          name="Despesas"))
    fig2.update_layout(barmode="stack",
                       title="Cascata: pagamentos mensais (R$ mi)",
                       xaxis_title="Mês", height=380,
                       legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig2, width="stretch")

if res["gatilho_mes"]:
    st.warning(f"⚡ Gatilho de subordinação mínima acionado no mês "
               f"{res['gatilho_mes']}: revolvência interrompida e "
               "amortização antecipada por senioridade neste cenário.")

g3, g4 = st.columns(2)
with g3:
    fig_sub = go.Figure()
    fig_sub.add_trace(go.Scatter(
        x=fluxo["mes"], y=fluxo["indice_subordinacao"] * 100,
        name="Índice de subordinação", line=dict(color="#0B5563")))
    if sub_min_pct > 0:
        fig_sub.add_hline(y=sub_min_pct, line_dash="dash", line_color="#B33A3A",
                          annotation_text="mínimo (gatilho)")
    if res["gatilho_mes"]:
        fig_sub.add_vline(x=res["gatilho_mes"], line_dash="dot",
                          annotation_text="gatilho")
    fig_sub.update_layout(title="Índice de subordinação dinâmico (%)",
                          xaxis_title="Mês", height=340)
    st.plotly_chart(fig_sub, width="stretch")

with g4:
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(x=fluxo["mes"], y=fluxo["perdas_acum"] / 1e6,
                              name="Perdas acumuladas", fill="tozeroy",
                              line=dict(color="#B33A3A")))
    fig3.add_hline(y=e.pl_total * pct_sub / 100 / 1e6, line_dash="dash",
                   annotation_text="Subordinada inicial")
    fig3.update_layout(
        title="Perdas acumuladas vs colchão de subordinação (R$ mi)",
        xaxis_title="Mês", height=340)
    st.plotly_chart(fig3, width="stretch")

# ------------------------------------------- suporte da estrutura (comitê)
st.divider()
try:
    st.page_link("pages/5_🎲_Simulacao_de_Retornos.py",
                 label="🎲 Ver distribuição de retorno das cotas (Monte Carlo) →")
except Exception:
    st.caption("🎲 Abra **Simulação de Retornos** no menu para ver a "
               "distribuição de TIR desta estrutura (Monte Carlo).")

# --------------------------------------------------------- sensibilidade
st.divider()
st.subheader("Análise de sensibilidade — onde vale a pena negociar")
st.caption("Cada parâmetro varia -X%/+X% (a taxa de cessão, ±15%; os "
          "demais, ±30%) e mostra o quanto move o retorno da subordinada. "
          "Quanto maior a barra, mais a estrutura é sensível àquele "
          "parâmetro — é ali que uma negociação com o originador rende "
          "mais.")
with st.spinner("Calculando sensibilidade..."):
    tor = tornado(e, alvo="tir_sub")
if tor.empty:
    st.info("Não foi possível calcular a sensibilidade para esta estrutura.")
else:
    fig_tor = go.Figure()
    fig_tor.add_trace(go.Bar(
        y=tor["parametro"],
        x=(tor["metrica_alto"] - tor["metrica_base"]) * 100,
        orientation="h", name="Cenário otimista",
        marker_color="#1C6B2E",
        customdata=tor["valor_alto"],
        hovertemplate="%{y}: %{customdata:.4g}<extra></extra>"))
    fig_tor.add_trace(go.Bar(
        y=tor["parametro"],
        x=(tor["metrica_baixo"] - tor["metrica_base"]) * 100,
        orientation="h", name="Cenário pessimista",
        marker_color="#B33A3A",
        customdata=tor["valor_baixo"],
        hovertemplate="%{y}: %{customdata:.4g}<extra></extra>"))
    fig_tor.update_layout(
        title="Impacto no retorno da subordinada (pontos percentuais de "
              "múltiplo sobre o aporte)",
        barmode="overlay", height=320,
        xaxis_title="Variação no retorno da sub (p.p.)",
        legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig_tor, width="stretch")

st.subheader("Suporte da estrutura — a pergunta do comitê")
st.caption("Até quanto o fundo aguenta antes de a classe mais sênior sofrer "
           "perda, no cenário parametrizado (incluindo o gatilho, se ativo).")
with st.spinner("Varrendo cenários de stress..."):
    sup = analise_suporte(e)
    cv = curva_stress(e)
if sup["breakeven_mult"] is None:
    st.error("A classe mais sênior sofre perda já no cenário base — "
             "não há colchão a medir. Reveja a estrutura.")
else:
    be_txt = (f"≥ {sup['breakeven_mult']:.0f}x"
              if sup["breakeven_mult"] >= 29.9
              else f"{sup['breakeven_mult']:.1f}x")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Suporta até", f"{be_txt} a inad. base")
    s2.metric("Inadimplência equivalente",
              f"{sup['inad_am_equivalente']*100:.2f}% a.m.")
    s3.metric("Perda absorvida no limite",
              f"R$ {sup['perda_maxima']/1e6:,.1f} mi")
    s4.metric("Perda no limite / PL",
              f"{sup['perda_maxima_pct_pl']*100:.1f}%")
    fig_cv = go.Figure()
    for c in [c.nome for c in classes]:
        fig_cv.add_trace(go.Scatter(
            x=cv["stress"], y=cv[f"recup_{c}"] * 100, name=c))
    fig_cv.add_vline(x=min(sup["breakeven_mult"], float(cv["stress"].max())),
                     line_dash="dash", line_color="#B33A3A",
                     annotation_text="break-even sênior")
    fig_cv.update_layout(
        title="Recuperação por classe conforme o stress (% do devido; "
              "subordinada: % do aporte)",
        xaxis_title="Stress sobre a inadimplência base (x)",
        yaxis_title="%", height=380,
        legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig_cv, width="stretch")
    st.caption("Leitura para o comitê: a estrutura suporta perder até "
               f"R$ {sup['perda_maxima']/1e6:,.1f} mi "
               f"({sup['perda_maxima_pct_pl']*100:.1f}% do PL) em créditos "
               "antes de a classe mais sênior deixar de receber o prometido. "
               "As classes intermediárias absorvem perdas na ordem inversa "
               "de senioridade, como mostra a curva.")

with st.expander("Fluxo mês a mês (dados da simulação)"):
    st.dataframe(fluxo, hide_index=True, width="stretch")

# --------------------------------------------------- aprovar e virar um deal
st.divider()
st.subheader("Aprovar estrutura e abrir esteira de constituição")
origs = [o for o in db.listar_originadores()]
if origs:
    ca, cb = st.columns([2, 2])
    orig_sel = ca.selectbox("Originador", [o["id"] for o in origs],
                            format_func=lambda i: next(
                                o["razao_social"] for o in origs
                                if o["id"] == i))
    st.session_state["ultimo_originador_id"] = orig_sel
    nome_fundo = cb.text_input("Nome do fundo",
                               value="FIDC " + next(
                                   o["razao_social"].split()[0]
                                   for o in origs if o["id"] == orig_sel))

    fp_atual = (round(pl), tuple((c.nome, round(c.pct, 4), round(c.taxa_am, 6))
                                 for c in classes), round(t_ces, 6),
               prazo, revolv, round(inad, 6), round(stress, 2))
    mc_res = st.session_state.get("mc_resultado")
    mc_fp = st.session_state.get("mc_fingerprint")
    mc_stats_memo = mc_res[1] if mc_res and mc_fp == fp_atual else None

    orig_obj = db.obter_originador(orig_sel) or {}
    calib_memo = db.obter_calibracao(orig_sel)

    cc, cd = st.columns(2)
    docx_bytes = gerar_memorando(
        nome_fundo=nome_fundo, originador=orig_obj, estrutura=e,
        resumo=res, por_classe=pc, suporte=sup, calibracao=calib_memo,
        mc_stats=mc_stats_memo, ratings=ratings)
    cc.download_button(
        "📄 Baixar memorando de comitê (Word)", data=docx_bytes,
        file_name=f"memorando_{nome_fundo.replace(' ', '_')}.docx",
        mime="application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document",
        width="stretch")
    if mc_stats_memo is None:
        cc.caption("💡 Rode a Simulação de Retornos para esta estrutura "
                  "antes de gerar o memorando e incluir a distribuição de "
                  "TIR (Monte Carlo).")
    if cd.button("Aprovar →", width="stretch",
                 disabled=not res["todas_integras"]):
        params = dict(pl_total=pl, taxa_cessao_am=t_ces,
                      prazo_medio_meses=prazo, meses_revolvencia=revolv,
                      inadimplencia_am=inad, prepagamento_am=prep,
                      recuperacao=recup, custos_aa=custos, cdi_aa=cdi,
                      stress=stress,
                      sub_minima=(sub_min_pct / 100) if sub_min_pct > 0 else None,
                      classes=[{"nome": c.nome, "pct": c.pct,
                                "taxa_am": c.taxa_am,
                                "residual": c.residual} for c in classes],
                      resumo=res)
        deal_id = db.criar_deal(orig_sel, nome_fundo, params)
        db.mover_etapa(orig_sel, "Aprovado")
        st.success(f"Deal #{deal_id} criado com os parâmetros desta "
                   "simulação. Acompanhe na Esteira de Constituição.")
    if not res["todas_integras"]:
        st.caption("A aprovação fica bloqueada enquanto houver classe com "
                   "perda no cenário simulado.")
else:
    st.info("Cadastre um originador no funil para vincular a estrutura.")
