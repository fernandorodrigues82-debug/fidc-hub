import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
from engine.cdi_api import buscar_cdi_atual
from engine.conceitos import ajuda
from engine.perda_bullet import ParametrosBullet, calcular_cenario, curva_perda, pontos_de_ruptura

st.set_page_config(page_title="Modelagem de perdas", page_icon="📉", layout="wide")
db.init_db()

st.title("Modelagem de perdas")
st.caption(
    "Cenário único (\"bullet\"): quanto de perda a estrutura aguenta antes de "
    "cada classe começar a ser afetada — o formato rápido de leitura em "
    "comitê. Complementar ao **Simulador de Estruturação**, que modela o "
    "fluxo mês a mês; aqui a carteira é cedida de uma vez, rende a taxa de "
    "cessão o prazo inteiro, e o PL Final resultante é distribuído de uma "
    "vez pela cascata Sênior → Mezanino → Júnior.")


def _num(valor, default=0.0):
    """st.number_input pode retornar None transitoriamente (campo vazio no
    meio da digitação) — normaliza para não quebrar contas mais adiante."""
    return valor if valor is not None else default


def _atualizar_cdi():
    r = buscar_cdi_atual()
    if r["ok"]:
        st.session_state["cdi_aa_perdas"] = round(r["valor"], 2)
        db.salvar_cache_indice("cdi_aa", r["valor"], r["data_referencia"].isoformat())
        st.session_state["_cdi_status_perdas"] = (
            "ok", "CDI atualizado via Bacen (SGS) — referência "
                 f"{r['data_referencia'].strftime('%d/%m/%Y')}.")
        return
    cache = db.obter_cache_indice("cdi_aa")
    if cache:
        st.session_state["_cdi_status_perdas"] = (
            "aviso", f"Bacen indisponível ({r['erro']}). Valor do campo não "
                    f"foi alterado — último dado confiável: {cache['valor']:.2f}% "
                    f"a.a. em {cache['data_referencia']}.")
    else:
        st.session_state["_cdi_status_perdas"] = (
            "erro", f"Não foi possível buscar o CDI ({r['erro']}). Ajuste "
                    "manualmente.")


# ---------------------------------------------------------- originador (opcional)
origs = db.listar_originadores()
ids_orig = [None] + [o["id"] for o in origs]
idx_default = 0
ativo = st.session_state.get("originador_ativo_perdas")
if ativo in ids_orig:
    idx_default = ids_orig.index(ativo)
sel_orig = st.selectbox(
    "Puxar dados de um originador (opcional)", options=ids_orig,
    index=idx_default, key="sel_originador_perdas",
    format_func=lambda i: "— nenhum —" if i is None
    else next(o["razao_social"] for o in origs if o["id"] == i),
    help="Se o originador tiver PL alvo cadastrado ou calibração de carteira "
         "feita no funil, uso como sugestão de partida — os campos continuam "
         "editáveis.")
st.session_state["originador_ativo_perdas"] = sel_orig

# ao trocar de originador, traz PL alvo e sugestão de perda base (via
# calibração) para os campos -- só na troca, pra não brigar com valor que o
# usuário já tenha ajustado manualmente. Setar o session_state ANTES do
# widget correspondente ser instanciado mais abaixo é o que faz efeito;
# mudar apenas o value= do widget é ignorado depois que ele já tem estado.
if (sel_orig is not None
        and st.session_state.get("_ultimo_orig_prefill_perdas") != sel_orig):
    o_ativo = db.obter_originador(sel_orig)
    calib_ativo = db.obter_calibracao(sel_orig)
    aplicados = []
    if o_ativo.get("pl_alvo"):
        st.session_state["pl_perdas"] = float(o_ativo["pl_alvo"])
        aplicados.append(f"PL alvo R$ {o_ativo['pl_alvo']/1e6:,.1f} mi")
    if calib_ativo and calib_ativo.get("taxa_media_am") is not None:
        prazo_atual = float(st.session_state.get("prazo_perdas", 12))
        m = calib_ativo["taxa_media_am"]
        sugestao = 1 - (1 - m) ** prazo_atual
        st.session_state["perda_base_perdas"] = round(sugestao * 100, 2)
        aplicados.append(
            f"perda base {sugestao*100:.2f}% (calibração real, "
            f"{calib_ativo['n_meses']} meses de histórico, "
            f"{m*100:.3f}% a.m.)")
    if aplicados:
        st.info(f"📋 Aplicado do cadastro de {o_ativo['razao_social']}: "
               + " · ".join(aplicados) + ". Ajuste os controles abaixo se "
               "quiser.")
st.session_state["_ultimo_orig_prefill_perdas"] = sel_orig

originador = db.obter_originador(sel_orig) if sel_orig else None
calib = db.obter_calibracao(sel_orig) if sel_orig else None
pl_sugerido = float(originador.get("pl_alvo") or 30e6) if originador else 30e6

st.divider()

# ---------------------------------------------------------------- estrutura
st.subheader("Estrutura e prazo")
c1, c2, c3 = st.columns(3)
pl_inicial = _num(c1.number_input(
    "PL Inicial (R$)", min_value=1.0, step=1e6, format="%.0f",
    value=pl_sugerido, key="pl_perdas", help=ajuda("pl_total")), 30e6)
prazo_meses = _num(c2.number_input(
    "Prazo do fundo (meses)", min_value=1.0, step=1.0, value=12.0,
    key="prazo_perdas",
    help="Horizonte único modelado — a carteira é cedida uma vez e rende a "
         "taxa de cessão por este prazo inteiro antes da liquidação final. "
         "Toda taxa anual (CDI, spreads) é composta por este prazo."), 12.0)
pct_caixa = _num(c3.number_input(
    "% do PL em caixa (não em DCs)", min_value=0.0, max_value=100.0,
    step=1.0, value=10.0, key="pct_caixa_perdas",
    help="Fração do PL que fica em caixa rendendo CDI em vez de comprar "
         "direitos creditórios.") / 100, 0.10)

c4, c5, c6 = st.columns(3)
pct_senior = _num(c4.number_input(
    "Cota Sênior (% do PL)", min_value=0.0, max_value=100.0, step=1.0,
    value=75.0, key="pct_sr_perdas", help=ajuda("senior")), 75.0) / 100
pct_mezanino = _num(c5.number_input(
    "Cota Mezanino (% do PL)", min_value=0.0, max_value=100.0, step=1.0,
    value=10.0, key="pct_meza_perdas", help=ajuda("mezanino")), 10.0) / 100
pct_junior = max(0.0, 1 - pct_senior - pct_mezanino)
c6.metric("Cota Júnior (resíduo)", f"{pct_junior*100:.1f}%", help=ajuda("junior"))
if pct_senior + pct_mezanino > 1:
    st.error("Sênior + Mezanino somam mais que 100% do PL — reduza algum dos dois.")
    st.stop()

st.divider()

# ------------------------------------------------------------ premissas de mercado
st.subheader("Premissas de mercado")
c7, c8 = st.columns([3, 1])
cdi_aa = _num(c7.number_input(
    "CDI projetado (% a.a.)", min_value=0.0, step=0.25, key="cdi_aa_perdas",
    help="Usado para render o caixa e como base dos spreads de Sênior/"
         "Mezanino."), 12.0)
c8.write("")
c8.button("🔄 Atualizar", key="btn_cdi_perdas", on_click=_atualizar_cdi,
          width="stretch")
if "_cdi_status_perdas" in st.session_state:
    nivel, msg = st.session_state["_cdi_status_perdas"]
    {"ok": st.success, "aviso": st.warning, "erro": st.error}[nivel](msg)

c9, c10, c11 = st.columns(3)
ajuste_curva = _num(c9.number_input(
    "Ajuste de curva (p.p. aa)", step=0.10, key="ajuste_curva_perdas",
    help="Prêmio/desconto sobre o CDI projetado para aproximar a curva de "
         "juros futura real, em vez de assumir CDI constante."), 0.0) / 100
spread_sr = _num(c10.number_input(
    "Spread Sênior sobre CDI (p.p. aa)", step=0.25, value=2.0,
    key="spread_sr_perdas",
    help="SÓ o spread — o CDI entra separado. Custo cheio da Sênior = "
         "CDI (+ ajuste) + este spread."), 2.0) / 100
spread_meza = _num(c11.number_input(
    "Spread Mezanino sobre CDI (p.p. aa)", step=0.25, value=3.0,
    key="spread_meza_perdas",
    help="SÓ o spread — mesma lógica da Sênior."), 3.0) / 100

c12, c13 = st.columns(2)
modo_cessao = c12.radio("Taxa de cessão", ["% a.m.", "CDI + spread (a.a.)"],
                        key="modo_cessao_perdas", horizontal=True)
cdi_efetivo_aa = cdi_aa / 100 + ajuste_curva
if modo_cessao == "% a.m.":
    taxa_cessao_am = _num(c13.number_input(
        "Taxa de cessão (% a.m.)", min_value=0.0, step=0.25, value=2.5,
        key="taxa_cessao_am_perdas",
        help="Taxa de desconto aplicada à carteira de direitos creditórios "
             "na cessão, ao mês."), 2.5) / 100
else:
    spread_cessao = _num(c13.number_input(
        "Cessão: CDI + (% a.a.)", step=0.25, value=6.0,
        key="spread_cessao_perdas"), 6.0) / 100
    taxa_cessao_am = (1 + cdi_efetivo_aa + spread_cessao) ** (1 / 12) - 1
taxa_cessao_aa_equiv = (1 + taxa_cessao_am) ** 12 - 1
st.caption(f"≈ {taxa_cessao_am*100:.3f}% a.m. · {taxa_cessao_aa_equiv*100:.2f}% a.a. equivalente")

custo_fidc_aa = _num(st.number_input(
    "Custo FIDC — taxa de administração e despesas (% a.a. sobre o PL)",
    min_value=0.0, step=0.05, value=1.35, key="custo_fidc_perdas"), 1.35) / 100

st.divider()

# ---------------------------------------------------------------- perda base
st.subheader("Cenário Base")
sugestao_base = None
if calib and calib.get("taxa_media_am") is not None:
    m = calib["taxa_media_am"]
    sugestao_base = 1 - (1 - m) ** prazo_meses
    st.caption(
        f"Sugestão a partir da calibração real da carteira de "
        f"**{originador['razao_social']}** ({calib['n_meses']} meses de "
        f"histórico, taxa média {m*100:.3f}% a.m.) composta pelo prazo do "
        f"fundo: **{sugestao_base*100:.2f}%**. Ajuste se achar necessário — "
        f"é uma sugestão, não uma trava.")
perda_base_pct = _num(st.number_input(
    "Perda assumida no cenário Base (% da carteira)", min_value=0.0,
    max_value=100.0, step=0.1,
    value=round((sugestao_base or 0.005) * 100, 2),
    key="perda_base_perdas",
    help="Premissa de perda esperada/histórica para o cenário Base. Os "
         "outros 3 cenários são calculados, não digitados — são os "
         "limiares em que Júnior e Mezanino se esgotam."), 0.5) / 100

params = ParametrosBullet(
    pl_inicial=pl_inicial, pct_senior=pct_senior, pct_mezanino=pct_mezanino,
    pct_caixa=pct_caixa, prazo_meses=prazo_meses, taxa_cessao_am=taxa_cessao_am,
    cdi_aa=cdi_aa / 100, ajuste_curva_aa=ajuste_curva,
    spread_senior_aa=spread_sr, spread_mezanino_aa=spread_meza,
    custo_fidc_aa=custo_fidc_aa, perda_base_pct=perda_base_pct,
)

st.divider()

# ---------------------------------------------------------------- cenários
st.subheader("Os 4 cenários")
cenarios = pontos_de_ruptura(params)

cols = st.columns(len(cenarios))
for col, c in zip(cols, cenarios):
    with col:
        st.metric(c["nome"], f"{c['perda_pct']*100:.1f}% de perda")
        st.caption(c["descricao"])
        st.write(f"PL Final: R$ {c['pl_final']/1e6:,.1f} mi")
        st.write(f"Sênior: {(c['pct_sr'] or 0)*100:.0f}% · "
                f"Mezanino: {(c['pct_meza'] or 0)*100:.0f}% · "
                f"Júnior: {(c['pct_jr'] or 0)*100:.0f}%")

st.divider()
st.subheader("Ponte de valor por cenário")
abas = st.tabs([c["nome"] for c in cenarios])
for aba, c in zip(abas, cenarios):
    with aba:
        fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=["absolute", "relative", "relative", "relative", "relative", "total"],
            x=["PL Inicial", "Rentabilidade", "Perdas", "Custos FIDC",
               "Remuneração Sr+Meza", "PL Final"],
            y=[params.pl_inicial / 1e6, c["rentabilidade"] / 1e6,
               -c["perdas_valor"] / 1e6, -c["custo_fundo"] / 1e6,
               -c["remuneracao_total"] / 1e6, 0],
            connector={"line": {"color": "rgba(100,100,100,0.4)"}},
            decreasing={"marker": {"color": "#B23A48"}},
            increasing={"marker": {"color": "#0B5563"}},
            totals={"marker": {"color": "#1C2B2D"}},
        ))
        fig.update_layout(height=380, showlegend=False,
                          title=f"{c['nome']} — {c['perda_pct']*100:.1f}% de perda "
                                "(R$ mi)")
        st.plotly_chart(fig, width="stretch")
        m1, m2, m3 = st.columns(3)
        m1.metric("Sênior", f"R$ {c['sr_final']/1e6:,.1f} mi",
                  f"{(c['pct_sr'] or 0)*100:.1f}% do valor de face")
        m2.metric("Mezanino", f"R$ {c['meza_final']/1e6:,.1f} mi",
                  f"{(c['pct_meza'] or 0)*100:.1f}% do valor de face")
        m3.metric("Júnior", f"R$ {c['jr_final']/1e6:,.1f} mi",
                  f"{(c['retorno_jr'] or 0)*100:+.1f}% de retorno")

st.divider()

# ---------------------------------------------------------------- curva continua
st.subheader("Curva de perda × recuperação por classe")
st.caption(
    "Mesma lógica dos 4 cenários, mas contínua — mostra exatamente onde cada "
    "ruptura acontece, em vez de só 4 pontos.")
curva = curva_perda(params, passos=101)
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=curva["perda_pct"] * 100, y=curva["pct_sr"] * 100,
                          name="Sênior", line=dict(color="#0B5563", width=3)))
fig2.add_trace(go.Scatter(x=curva["perda_pct"] * 100, y=curva["pct_meza"] * 100,
                          name="Mezanino", line=dict(color="#B58900", width=3)))
fig2.add_trace(go.Scatter(x=curva["perda_pct"] * 100, y=curva["pct_jr"].clip(lower=0) * 100,
                          name="Júnior", line=dict(color="#B23A48", width=3)))
for c in cenarios[1:]:
    fig2.add_vline(x=c["perda_pct"] * 100, line_dash="dot",
                   line_color="rgba(100,100,100,0.5)",
                   annotation_text=c["nome"], annotation_position="top")
fig2.update_layout(height=420, xaxis_title="Perda sobre a carteira (%)",
                   yaxis_title="Recuperação da classe (%)",
                   legend=dict(orientation="h", y=-0.2))
st.plotly_chart(fig2, width="stretch")
