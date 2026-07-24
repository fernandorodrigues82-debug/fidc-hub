import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
from engine.cdi_api import buscar_cdi_atual
from engine.curva_pre import taxa_pre_no_prazo
from engine.conceitos import ajuda
from engine.perda_bullet import (ParametrosBullet, PoolRisco, calcular_cenario,
                                 cenario_sistemico, choque_idiossincratico,
                                 curva_multiplicador_pools, curva_perda,
                                 pontos_de_ruptura, pontos_de_ruptura_pools)

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


def _buscar_curva_pre_perdas():
    """Callback do botão 'Buscar' (ajuste de curva). Este modelo é bullet
    (cessão única pelo prazo do fundo inteiro) -- usa prazo_perdas (em
    meses, convertido a dias) como o prazo de referência na curva, já que
    não há aqui um campo separado de prazo médio dos recebíveis."""
    prazo_meses_atual = float(st.session_state.get("prazo_perdas", 12))
    cdi_atual = float(st.session_state.get("cdi_aa_perdas", 12.0))
    r = taxa_pre_no_prazo(prazo_meses_atual * 30)
    if not r["ok"]:
        st.session_state["_curva_status_perdas"] = (
            "erro", f"Não foi possível buscar a curva da B3 ({r['erro']}). "
                    "Ajuste manualmente.")
        return
    ajuste = round(r["taxa"] * 100 - cdi_atual, 2)
    st.session_state["ajuste_curva_perdas"] = ajuste
    fonte = ("curva de " + r["data_curva"] if r["fonte"] == "b3"
            else "última curva salva, B3 indisponível agora")
    nivel = "ok" if r["fonte"] == "b3" else "aviso"
    msg = (f"Pré B3 em {prazo_meses_atual*30:.0f} dias: {r['taxa']*100:.2f}% "
          f"aa ({fonte}) → ajuste de {ajuste:+.2f} p.p. sobre o CDI aplicado.")
    if r.get("aviso"):
        msg += " " + r["aviso"]
    st.session_state["_curva_status_perdas"] = (nivel, msg)


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
    "CDI projetado (% a.a.)", min_value=0.0, step=0.25, value=12.0,
    key="cdi_aa_perdas",
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
         "juros futura real, em vez de assumir CDI constante. Pode "
         "digitar direto ou clicar em 'Buscar curva B3' abaixo para puxar "
         "da curva Pré real, interpolada no prazo do fundo."), 0.0) / 100
spread_sr = _num(c10.number_input(
    "Spread Sênior sobre CDI (p.p. aa)", step=0.25, value=2.0,
    key="spread_sr_perdas",
    help="SÓ o spread — o CDI entra separado. Custo cheio da Sênior = "
         "CDI (+ ajuste) + este spread."), 2.0) / 100
spread_meza = _num(c11.number_input(
    "Spread Mezanino sobre CDI (p.p. aa)", step=0.25, value=3.0,
    key="spread_meza_perdas",
    help="SÓ o spread — mesma lógica da Sênior."), 3.0) / 100
st.button("🔄 Buscar curva B3 (pré no prazo do fundo)", key="btn_curva_pre_perdas",
         on_click=_buscar_curva_pre_perdas)
if "_curva_status_perdas" in st.session_state:
    nivel, msg = st.session_state["_curva_status_perdas"]
    {"ok": st.success, "aviso": st.warning, "erro": st.error}[nivel](msg)
cdi_efetivo_aa = cdi_aa / 100 + ajuste_curva
st.caption(
    f"Pré-equivalente (dado o CDI acima): Sênior ≈ "
    f"{(cdi_efetivo_aa+spread_sr)*100:.2f}% aa · Mezanino ≈ "
    f"{(cdi_efetivo_aa+spread_meza)*100:.2f}% aa")

c12, c13 = st.columns(2)
modo_cessao = c12.radio("Taxa de cessão", ["% a.m.", "CDI + spread (a.a.)"],
                        key="modo_cessao_perdas", horizontal=True)
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
spread_cessao_equiv = (taxa_cessao_aa_equiv - cdi_efetivo_aa) * 100
sinal_cessao = "+" if spread_cessao_equiv >= 0 else ""
st.caption(
    f"≈ {taxa_cessao_am*100:.3f}% a.m. · {taxa_cessao_aa_equiv*100:.2f}% a.a. "
    f"pré · equivalente a CDI{sinal_cessao}{spread_cessao_equiv:.2f} p.p. a.a.")

custo_fidc_aa = _num(st.number_input(
    "Custo FIDC — taxa de administração e despesas (% a.a. sobre o PL)",
    min_value=0.0, step=0.05, value=1.35, key="custo_fidc_perdas"), 1.35) / 100

st.divider()

# ---------------------------------------------------------------- perda esperada
st.subheader("Perda esperada")
modo_carteira = st.radio(
    "Carteira", ["Única (perda-base homogênea)", "Segmentar por pool de risco"],
    key="modo_carteira_perdas", horizontal=True,
    help="Use 'Segmentar' quando a carteira tiver perfis de risco bem "
         "diferentes dentro do mesmo fundo — por exemplo, uma fatia "
         "pulverizada em sacados de alta qualidade e outra fatia de risco "
         "do próprio originador. Uma taxa de perda única mascara essa "
         "diferença.")

pools: list[PoolRisco] = []
perdas_base_por_pool: dict[str, float] = {}
sugestao_base = None
if calib and calib.get("taxa_media_am") is not None:
    m = calib["taxa_media_am"]
    sugestao_base = 1 - (1 - m) ** prazo_meses

if modo_carteira == "Única (perda-base homogênea)":
    if sugestao_base is not None:
        st.caption(
            f"Sugestão a partir da calibração real da carteira de "
            f"**{originador['razao_social']}** ({calib['n_meses']} meses de "
            f"histórico, taxa média {m*100:.3f}% a.m.) composta pelo prazo "
            f"do fundo: **{sugestao_base*100:.2f}%**. Ajuste se achar "
            f"necessário — é uma sugestão, não uma trava.")
    perda_base_pct = _num(st.number_input(
        "Perda assumida no cenário Base (% da carteira)", min_value=0.0,
        max_value=100.0, step=0.1,
        value=round((sugestao_base or 0.005) * 100, 2),
        key="perda_base_perdas",
        help="Premissa de perda esperada/histórica para o cenário Base. Os "
             "outros 3 cenários são calculados, não digitados — são os "
             "limiares em que Júnior e Mezanino se esgotam."), 0.5) / 100
else:
    st.caption(
        "Cada linha é uma fatia da carteira de direitos creditórios (não do "
        "PL) — a soma de '% da carteira' deve fechar 100%.")
    default_pools = pd.DataFrame([
        {"nome": "Sacados", "% da carteira": 70.0, "tipo de risco": "sacado",
         "regime jurídico": "true_sale", "nº de contrapartes": 10,
         "rating": "AAA", "perda base (%)": 0.3, "correlação macro (%)": 30.0},
        {"nome": "Originador", "% da carteira": 30.0, "tipo de risco": "originador",
         "regime jurídico": "coobrigacao", "nº de contrapartes": 1,
         "rating": "Sem rating", "perda base (%)": 2.0, "correlação macro (%)": 90.0},
    ])
    editado = st.data_editor(
        st.session_state.get("tabela_pools_perdas", default_pools),
        key="tabela_pools_perdas", num_rows="dynamic", width="stretch",
        column_config={
            "tipo de risco": st.column_config.SelectboxColumn(
                options=["sacado", "originador"]),
            "regime jurídico": st.column_config.SelectboxColumn(
                options=["true_sale", "coobrigacao"]),
            "% da carteira": st.column_config.NumberColumn(min_value=0.0, max_value=100.0),
            "perda base (%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0),
            "correlação macro (%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0),
            "nº de contrapartes": st.column_config.NumberColumn(min_value=1, step=1),
        })

    soma_pct = editado["% da carteira"].sum()
    if abs(soma_pct - 100) > 0.5:
        st.error(f"Os pools somam {soma_pct:.1f}% da carteira — ajuste para "
                "fechar 100%.")
        st.stop()

    for _, linha in editado.iterrows():
        n_contrap = linha["nº de contrapartes"]
        pools.append(PoolRisco(
            nome=str(linha["nome"]),
            pct_carteira=float(linha["% da carteira"]) / 100,
            tipo_risco=str(linha["tipo de risco"]),
            regime_juridico=str(linha["regime jurídico"]),
            n_contrapartes=int(n_contrap) if pd.notna(n_contrap) else None,
            rating=str(linha["rating"]) if pd.notna(linha["rating"]) else None,
            perda_base_pct=float(linha["perda base (%)"]) / 100,
            correlacao_macro=float(linha["correlação macro (%)"]) / 100,
        ))
    perdas_base_por_pool = {p_.nome: p_.perda_base_pct for p_ in pools}

    # concentração por nome -- não há limite numérico fixo da CVM (ver nota
    # abaixo), mas é um dos principais indicadores de risco em crédito
    # estruturado e vale sinalizar quando um nome pesa muito na carteira
    for p_ in pools:
        if p_.risco_dobrado:
            st.warning(
                f"⚠️ **{p_.nome}** ({p_.pct_carteira*100:.0f}% da carteira): "
                "risco do próprio originador COM coobrigação/recurso a ele "
                "mesmo. Isso não é uma segunda linha de defesa de verdade — "
                "se o originador tropeçar, a obrigação original e o recurso "
                "falham juntos, pela mesma causa.")
        if p_.n_contrapartes:
            conc_por_nome = p_.pct_carteira / p_.n_contrapartes
            if conc_por_nome >= 0.15:
                st.caption(
                    f"📋 {p_.nome}: em média, cada uma das "
                    f"{p_.n_contrapartes} contraparte(s) responde por "
                    f"~{conc_por_nome*100:.0f}% da carteira. A Res. CVM 175 "
                    "(Anexo Normativo II) não fixa um percentual único de "
                    "concentração por sacado/devedor — é principiológica: "
                    "exige que o risco seja identificado, mensurado e "
                    "divulgado, com o limite definido no regulamento do "
                    "fundo (política de investimento, subordinação, "
                    "público-alvo). Como referência de mercado, é comum "
                    "ver ~5-10% por sacado em carteiras pulverizadas e "
                    "~15-20% em carteiras corporativas — concentração "
                    "maior é possível em fundos mono/poucos sacados, desde "
                    "que prevista no regulamento e compatível com o perfil "
                    "de risco. Na prática, quanto maior a concentração, "
                    "mais subordinação, garantias ou covenants o mercado "
                    "tende a exigir, e mais isso pesa na nota de rating.")

    perda_base_pct = sum(p_.pct_carteira * p_.perda_base_pct for p_ in pools)

params = ParametrosBullet(
    pl_inicial=pl_inicial, pct_senior=pct_senior, pct_mezanino=pct_mezanino,
    pct_caixa=pct_caixa, prazo_meses=prazo_meses, taxa_cessao_am=taxa_cessao_am,
    cdi_aa=cdi_aa / 100, ajuste_curva_aa=ajuste_curva,
    spread_senior_aa=spread_sr, spread_mezanino_aa=spread_meza,
    custo_fidc_aa=custo_fidc_aa, perda_base_pct=perda_base_pct, pools=pools,
)

st.divider()

# ---------------------------------------------------------------- cenários
def _rotulo_severidade(c: dict) -> str:
    """No modo pools o rótulo é um multiplicador de severidade sobre a
    perda-base combinada; no modo única é a perda % direto."""
    if "multiplicador" in c:
        return f"{c['multiplicador']:.2f}x severidade"
    return f"{c['perda_pct']*100:.1f}% de perda"


st.subheader("Os 4 cenários")
if modo_carteira == "Única (perda-base homogênea)":
    cenarios = pontos_de_ruptura(params)
else:
    cenarios = pontos_de_ruptura_pools(params, perdas_base_por_pool)
    st.caption(
        "O multiplicador escala a perda-base de TODOS os pools ao mesmo "
        "tempo, na mesma proporção — é a leitura equivalente à perda % do "
        "modo único, adaptada para quando cada pool tem sua própria "
        "perda-base.")

cols = st.columns(len(cenarios))
for col, c in zip(cols, cenarios):
    with col:
        st.metric(c["nome"], _rotulo_severidade(c))
        st.caption(c["descricao"])
        st.write(f"PL Final: R$ {c['pl_final']/1e6:,.1f} mi")
        st.write(f"Sênior: {(c['pct_sr'] or 0)*100:.0f}% · "
                f"Mezanino: {(c['pct_meza'] or 0)*100:.0f}% · "
                f"Júnior: {(c['pct_jr'] or 0)*100:.0f}%")

if modo_carteira != "Única (perda-base homogênea)" and pools:
    st.subheader("Cenários adicionais de concentração")
    pools_com_n = [p_ for p_ in pools if p_.n_contrapartes and p_.n_contrapartes > 1]
    extras = []
    for p_ in pools_com_n:
        r = choque_idiossincratico(params, perdas_base_por_pool, p_.nome)
        if r:
            extras.append((f"Quebra do maior nome — {p_.nome}", r))
    choque_macro = st.slider(
        "Choque sistêmico adicional (%)", min_value=0, max_value=300,
        value=100, step=10, key="choque_macro_perdas",
        help="Multiplica a perda-base de cada pool pela sua correlação a um "
             "fator comum — pools mais correlacionados ao sistêmico pioram "
             "mais junto.") / 100
    extras.append(("Cenário sistêmico", cenario_sistemico(
        params, perdas_base_por_pool, choque_macro)))

    cols_extra = st.columns(len(extras))
    for col, (nome, r) in zip(cols_extra, extras):
        with col:
            st.metric(nome, f"R$ {r['pl_final']/1e6:,.1f} mi PL Final")
            st.caption(r["descricao"])
            st.write(f"Sênior: {(r['pct_sr'] or 0)*100:.0f}% · "
                    f"Mezanino: {(r['pct_meza'] or 0)*100:.0f}% · "
                    f"Júnior: {(r['pct_jr'] or 0)*100:.0f}%")

st.divider()
st.subheader("Ponte de valor por cenário")
abas = st.tabs([c["nome"] for c in cenarios])
for aba, c in zip(abas, cenarios):
    with aba:
        valores_mi = [params.pl_inicial / 1e6, c["rentabilidade"] / 1e6,
                     -c["perdas_valor"] / 1e6, -c["custo_fundo"] / 1e6,
                     -c["remuneracao_total"] / 1e6, c["pl_final"] / 1e6]
        textos = [f"{v:+,.1f}" if i not in (0, 5) else f"{v:,.1f}"
                 for i, v in enumerate(valores_mi)]
        fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=["absolute", "relative", "relative", "relative", "relative", "total"],
            x=["PL Inicial", "Rentabilidade", "Perdas", "Custos FIDC",
               "Remuneração Sr+Meza", "PL Final"],
            y=valores_mi[:-1] + [0],  # measure "total" recalcula o ultimo sozinho
            text=textos, textposition="outside",
            texttemplate="%{text}",
            connector={"line": {"color": "rgba(100,100,100,0.4)"}},
            decreasing={"marker": {"color": "#B23A48"}},
            increasing={"marker": {"color": "#0B5563"}},
            totals={"marker": {"color": "#1C2B2D"}},
        ))
        fig.update_layout(height=420, showlegend=False,
                          title=f"{c['nome']} — {_rotulo_severidade(c)} (R$ mi)",
                          margin=dict(t=80))
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
if modo_carteira == "Única (perda-base homogênea)":
    curva = curva_perda(params, passos=101)
    eixo_x = curva["perda_pct"] * 100
    eixo_titulo = "Perda sobre a carteira (%)"
    valor_linha = lambda c: c["perda_pct"] * 100
else:
    k_max = max(5.0, max((c.get("multiplicador") or 0) for c in cenarios) * 1.2)
    curva = curva_multiplicador_pools(params, perdas_base_por_pool, k_max=k_max, passos=101)
    eixo_x = curva["multiplicador"]
    eixo_titulo = "Multiplicador de severidade sobre a perda-base combinada"
    valor_linha = lambda c: c["multiplicador"]

fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=eixo_x, y=curva["pct_sr"] * 100,
                          name="Sênior", line=dict(color="#0B5563", width=3)))
fig2.add_trace(go.Scatter(x=eixo_x, y=curva["pct_meza"] * 100,
                          name="Mezanino", line=dict(color="#B58900", width=3)))
fig2.add_trace(go.Scatter(x=eixo_x, y=curva["pct_jr"].clip(lower=0) * 100,
                          name="Júnior", line=dict(color="#B23A48", width=3)))
for c in cenarios[1:]:
    fig2.add_vline(x=valor_linha(c), line_dash="dot",
                   line_color="rgba(100,100,100,0.5)",
                   annotation_text=c["nome"], annotation_position="top")
fig2.update_layout(height=420, xaxis_title=eixo_titulo,
                   yaxis_title="Recuperação da classe (%)",
                   legend=dict(orientation="h", y=-0.2))
st.plotly_chart(fig2, width="stretch")
