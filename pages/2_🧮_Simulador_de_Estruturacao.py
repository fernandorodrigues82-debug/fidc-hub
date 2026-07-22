import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
from engine.cdi_api import buscar_cdi_atual
from engine.conceitos import ajuda
from engine.parser_operacao import interpretar_llm, interpretar_local
from engine.exportar import gerar_excel
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


def _taxa_am(benchmark: str, valor: float, cdi_aa: float,
            ajuste_curva: float = 0.0) -> float:
    """ajuste_curva: prêmio/desconto (p.p. aa) sobre o CDI projetado, para
    aproximar a taxa efetiva da curva de mercado real (DI futuro) em vez de
    assumir CDI flat — só se aplica aos benchmarks atrelados a CDI."""
    cdi_efetivo = cdi_aa + ajuste_curva
    cdi_am = (1 + cdi_efetivo / 100) ** (1 / 12) - 1
    if benchmark == "CDI + spread (a.a.)":
        return (1 + (cdi_efetivo + valor) / 100) ** (1 / 12) - 1
    if benchmark == "% do CDI":
        return cdi_am * valor / 100
    if benchmark == "Prefixado (a.a.)":
        return (1 + valor / 100) ** (1 / 12) - 1
    return valor / 100  # Fixa (a.m.)


def _taxa_aa_equivalente(taxa_am: float) -> float:
    """Converte uma taxa mensal de volta para a anual equivalente — usado
    para mostrar o 'pré equivalente' de benchmarks atrelados a CDI."""
    return (1 + taxa_am) ** 12 - 1


def _num(valor, default=0.0):
    """st.number_input pode retornar None transitoriamente — por exemplo,
    no navegador real, enquanto a pessoa apaga o campo antes de digitar um
    valor novo. Normaliza para um número seguro em vez de deixar o None
    se propagar e quebrar contas mais adiante."""
    return valor if valor is not None else default


def _atualizar_cdi():
    """Callback do botão 'Atualizar CDI'. Roda antes do rerender do script,
    então já deixa cdi_aa pronto no session_state antes do number_input ser
    instanciado. Em caso de falha, NÃO mexe no valor atual do campo — só
    informa o problema (e, se houver, a data do último valor confiável)."""
    r = buscar_cdi_atual()
    if r["ok"]:
        st.session_state["cdi_aa"] = round(r["valor"], 2)
        db.salvar_cache_indice("cdi_aa", r["valor"],
                               r["data_referencia"].isoformat())
        st.session_state["_cdi_status"] = (
            "ok", "CDI atualizado via Bacen (SGS) — referência "
                 f"{r['data_referencia'].strftime('%d/%m/%Y')}.")
        return
    cache = db.obter_cache_indice("cdi_aa")
    if cache:
        st.session_state["_cdi_status"] = (
            "aviso", f"Bacen indisponível ({r['erro']}). Valor do campo "
                    "não foi alterado — último dado confiável do Bacen: "
                    f"{cache['valor']:.2f}% a.a. em "
                    f"{cache['data_referencia']} "
                    f"(buscado em {cache['atualizado_em']}).")
    else:
        st.session_state["_cdi_status"] = (
            "erro", f"Não foi possível buscar o CDI ({r['erro']}). "
                    "Ajuste o valor manualmente.")


CLASSES_PADRAO = pd.DataFrame([
    {"Classe": "Sênior", "% do PL": 75.0,
     "Benchmark": "CDI + spread (a.a.)", "Valor": 3.0},
    {"Classe": "Mezanino", "% do PL": 10.0,
     "Benchmark": "CDI + spread (a.a.)", "Valor": 6.0},
])

DEFAULTS = dict(pl=100e6, sub_min=12.0, cdi_aa=12.0, ajuste_curva=0.0,
                t_ces=2.20, modo_cessao="Taxa fixa (% a.m.)",
                prazo_dias=90, revolv=24, rampa=0, carencia=0,
                custo_inicial=0.0, custo_inicial_meses=1, prazo_maximo=0,
                prep=1.0, inad=0.80, recup=30, custos=1.20, stress=1.0)
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
if "classes_df" not in st.session_state:
    st.session_state["classes_df"] = CLASSES_PADRAO.copy()

# aplica parâmetros vindos do interpretador ANTES de criar os widgets
if pend := st.session_state.pop("_aplicar", None):
    simples = {"pl_total": ("pl", float), "cdi_aa": ("cdi_aa", float),
               "taxa_cessao_am": ("t_ces", lambda v: round(v * 100, 2)),
               "prazo_medio_meses": ("prazo_dias", lambda v: round(v * 30)),
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

# carrega uma simulação salva por completo (substitui todos os campos)
if pend_full := st.session_state.pop("_carregar_simulacao", None):
    campos_simples = ["pl", "cdi_aa", "ajuste_curva", "t_ces", "modo_cessao",
                      "prazo_dias", "revolv", "rampa", "carencia",
                      "custo_inicial", "custo_inicial_meses", "prazo_maximo",
                      "prep", "inad", "recup", "custos", "sub_min", "stress"]
    for k in campos_simples:
        if k in pend_full:
            st.session_state[k] = pend_full[k]
    if "classes_editor" in pend_full:
        st.session_state["classes_df"] = pd.DataFrame(pend_full["classes_editor"])
    st.session_state["sim_carregada_id"] = pend_full.get("_sim_id")
    st.session_state["sim_carregada_nome"] = pend_full.get("_sim_nome", "")

st.title("Simulador de estruturação")
st.caption("Peça de decisão do comitê: estrutura multiclasses com pagamento "
           "sequencial por senioridade, stress de inadimplência e break-even "
           "— antes de o banco comprometer capital como cotista.")

# --------------------------------------------------- originador vinculado
origs_topo = db.listar_originadores()
if origs_topo:
    ids_topo = [None] + [o["id"] for o in origs_topo]
    idx_default = 0
    ativo = st.session_state.get("originador_ativo")
    if ativo in ids_topo:
        idx_default = ids_topo.index(ativo)
    orig_ativo = st.selectbox(
        "Originador desta simulação",
        ids_topo, index=idx_default, key="sel_originador_topo",
        format_func=lambda i: "— nenhum (exploração livre) —" if i is None
        else next(o["razao_social"] for o in origs_topo if o["id"] == i))
    st.session_state["originador_ativo"] = orig_ativo

    # ao trocar de originador (e sem simulação já carregada), traz PL alvo
    # e rampa prometida do cadastro para os campos — só na troca, pra não
    # brigar com valores que o usuário já tenha ajustado manualmente
    if (orig_ativo is not None
            and st.session_state.get("_ultimo_orig_prefill") != orig_ativo
            and not st.session_state.get("sim_carregada_id")):
        o_ativo = db.obter_originador(orig_ativo)
        aplicados = []
        if o_ativo.get("pl_alvo"):
            st.session_state["pl"] = float(o_ativo["pl_alvo"])
            aplicados.append(f"PL alvo R$ {o_ativo['pl_alvo']/1e6:,.0f} mi")
        if o_ativo.get("meses_rampa"):
            revolv_atual = int(st.session_state.get("revolv", 24))
            st.session_state["rampa"] = min(int(o_ativo["meses_rampa"]),
                                            revolv_atual)
            aplicados.append(f"rampa {o_ativo['meses_rampa']:.0f} m")
        if aplicados:
            st.info(f"📋 Aplicado do cadastro de {o_ativo['razao_social']}: "
                   + " · ".join(aplicados) + ". Ajuste os controles abaixo "
                   "se quiser.")
    st.session_state["_ultimo_orig_prefill"] = orig_ativo
else:
    orig_ativo = None
    st.caption("Nenhum originador cadastrado ainda — cadastre um no Funil "
              "de Originadores para poder salvar simulações vinculadas.")

with st.expander("💾 Simulações salvas" +
                 (f" — {next(o['razao_social'] for o in origs_topo if o['id'] == orig_ativo)}"
                  if orig_ativo else ""),
                 expanded=bool(st.session_state.get("sim_carregada_id"))):
    if not orig_ativo:
        st.caption("Selecione um originador acima para ver e salvar "
                  "simulações vinculadas a ele.")
    else:
        sims = db.listar_simulacoes(orig_ativo)
        if sims:
            cl1, cl2 = st.columns([3, 1])
            sim_sel = cl1.selectbox(
                "Carregar simulação salva", [s["id"] for s in sims],
                format_func=lambda i: next(
                    f"{s['nome']} — {s['atualizado_em'][:16].replace('T', ' ')}"
                    for s in sims if s["id"] == i))
            if cl2.button("📂 Carregar", width="stretch"):
                params = json.loads(next(s["parametros"] for s in sims
                                         if s["id"] == sim_sel))
                params["_sim_id"] = sim_sel
                params["_sim_nome"] = next(s["nome"] for s in sims
                                           if s["id"] == sim_sel)
                st.session_state["_carregar_simulacao"] = params
                st.rerun()
        else:
            st.caption("Nenhuma simulação salva para este originador ainda.")

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
           "Sênior B, Mezanino. A júnior é o residual, calculado "
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
m_sub1.metric("Júnior (residual)", f"{pct_sub:.1f}%",
              help=ajuda("junior"))
if pct_sub <= 0:
    m_sub2.error("As classes somam ≥ 100% do PL — sobra nada para a "
                 "júnior. Reduza os percentuais.")
    st.stop()
if len(cdf) == 0:
    st.error("Inclua ao menos uma classe além da júnior.")
    st.stop()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.caption("📖 Dúvida em algum parâmetro? Toque no (?) de cada campo ou "
               "abra a página **Guia de Conceitos** no menu.")
    with st.expander("💰 Fundo e cenário", expanded=True):
        pl = _num(st.number_input("PL total (R$)", min_value=1e6, step=10e6,
                                  format="%.0f", key="pl",
                                  help=ajuda("pl_total")), 100e6)
        c_cdi1, c_cdi2 = st.columns([3, 1])
        cdi = _num(c_cdi1.number_input(
            "CDI projetado (% a.a.)", min_value=0.0, step=0.25, key="cdi_aa",
            help="Usado nos benchmarks CDI+ e % do CDI. Pode digitar direto "
                 "ou clicar em Atualizar para puxar o valor mais recente do "
                 "Bacen (SGS 4392 — CDI anualizado, base 252)."), 12.0)
        c_cdi2.write("")  # alinhar verticalmente o botão com o campo
        c_cdi2.button("🔄 Atualizar", key="btn_atualizar_cdi",
                      on_click=_atualizar_cdi, width="stretch")
        if "_cdi_status" in st.session_state:
            nivel, msg = st.session_state["_cdi_status"]
            {"ok": st.success, "aviso": st.warning,
             "erro": st.error}[nivel](msg, icon="✅" if nivel == "ok" else None)
        ajuste_curva = _num(st.number_input(
            "Ajuste de curva (p.p. aa)", step=0.10, key="ajuste_curva",
            help="Opcional: some (ou subtraia) sobre o CDI projetado para "
                 "aproximar a curva de juros futura real de mercado (DI "
                 "futuro), em vez de assumir CDI constante. Ex.: se o "
                 "mercado precifica queda de juros no prazo do fundo, use "
                 "um valor negativo. 0 = usa o CDI projetado direto (flat)."),
            0.0)
        modo_cessao = st.radio(
            "Como informar a taxa de cessão?",
            ["Taxa fixa (% a.m.)", "CDI + spread (a.a.)"],
            key="modo_cessao", horizontal=True)
        if modo_cessao == "CDI + spread (a.a.)":
            spread_cessao = _num(st.number_input(
                "Cessão: CDI + (% a.a.)", step=0.25, key="spread_cessao",
                help="Ex.: regra 'maior entre CDI+X e PDD+despesas' — "
                     "informe o X vencedor aqui."), 6.0)
            t_ces = _taxa_am("CDI + spread (a.a.)", spread_cessao, cdi,
                            ajuste_curva)
            st.caption(f"≈ {t_ces*100:.3f}% a.m. · "
                      f"{_taxa_aa_equivalente(t_ces)*100:.2f}% aa equivalente")
        else:
            t_ces = _num(st.number_input(
                "Taxa de cessão da carteira (% a.m.)", step=0.05,
                key="t_ces", help=ajuda("taxa_cessao")), 2.20) / 100
        custo_inicial = _num(st.number_input(
            "Custo inicial one-off (R$)", min_value=0.0, step=5000.0,
            key="custo_inicial",
            help="Custo único de estruturação (ex.: taxa de distribuição "
                 "flat sobre a cota sênior). Não é recorrente — some aqui "
                 "o valor total em R$."), 0.0)
        custo_inicial_meses = int(_num(st.number_input(
            "Diferir esse custo em quantos meses?", min_value=1, step=1,
            key="custo_inicial_meses",
            help="1 = todo o custo sai do caixa no mês 1. Um número maior "
                 "dilui o impacto — o custo total é o mesmo, mas o golpe "
                 "de caixa em qualquer mês individual é menor."), 1) or 1)
    with st.expander("📅 Carteira e prazos", expanded=False):
        prazo_dias = _num(st.number_input(
            "Prazo médio dos recebíveis (dias)", min_value=1, max_value=720,
            step=5, key="prazo_dias", help=ajuda("prazo_medio")), 90)
        st.caption(f"≈ {prazo_dias/30:.2f} meses")
        revolv = st.slider("Revolvência (meses)", 0, 60, key="revolv",
                           help=ajuda("revolvencia"))
        st.session_state["rampa"] = min(st.session_state.get("rampa", 0), revolv)
        rampa = st.slider(
            "Rampa de integralização (meses)", 0, max(revolv, 0),
            key="rampa", help=ajuda("rampa"))
        carencia = st.slider("Carência antes da amortização (meses)", 0, 24,
                             key="carencia", help=ajuda("carencia"))
        prazo_maximo = int(_num(st.number_input(
            "Prazo máximo do fundo (meses) — 0 = sem teto", min_value=0,
            step=1, key="prazo_maximo",
            help="Teto legal de duração do fundo. Se a amortização natural "
                 "ainda não tiver terminado nesse mês, o motor força a "
                 "liquidação da carteira remanescente e paga a cascata com "
                 "o que houver — util para checar se o cronograma cabe no "
                 "prazo do fundo. 0 desativa o teto (roda até quitar "
                 "naturalmente)."), 0) or 0)
        prep = _num(st.number_input("Pré-pagamento (% a.m.)", step=0.5,
                                    key="prep",
                                    help=ajuda("prepagamento")), 1.0) / 100
    with st.expander("⚠️ Risco", expanded=False):
        inad = _num(st.number_input(
            "Inadimplência base (% dos vencimentos/mês)", step=0.10,
            key="inad", help=ajuda("inadimplencia")), 0.80) / 100
        recup = st.slider("Recuperação de créditos vencidos (%)", 0, 80,
                          key="recup", help=ajuda("recuperacao")) / 100
        custos = _num(st.number_input("Custos do fundo (% PL a.a.)",
                                      step=0.10, key="custos",
                                      help=ajuda("custos")), 1.20) / 100
        stress = st.slider("Stress sobre a inadimplência (x)", 1.0, 15.0,
                           step=0.5, key="stress", help=ajuda("stress"))
        sub_min_pct = _num(st.number_input(
            "Subordinação mínima — gatilho (%)", min_value=0.0,
            max_value=60.0, step=1.0, key="sub_min",
            help="Evento de avaliação: se o índice de subordinação "
                 "dinâmico — (ativos − dívida das classes) / ativos — cair "
                 "abaixo deste mínimo antes da amortização, o fundo para "
                 "de reinvestir e amortiza antecipadamente, protegendo as "
                 "classes por senioridade. 0 = sem gatilho."), 12.0)

classes = [Classe(str(row["Classe"]), float(row["% do PL"]) / 100,
                  _taxa_am(row["Benchmark"], float(row["Valor"] or 0), cdi,
                          ajuste_curva))
           for _, row in cdf.iterrows()]
classes.append(Classe("Júnior", pct_sub / 100, 0.0, residual=True))

with st.expander("📐 Pré equivalente por classe (dado o CDI acima)",
                 expanded=False):
    linhas_pre = []
    for _, row in cdf.iterrows():
        if row["Benchmark"] in ("CDI + spread (a.a.)", "% do CDI"):
            ta = _taxa_am(row["Benchmark"], float(row["Valor"] or 0), cdi,
                         ajuste_curva)
            linhas_pre.append((row["Classe"], f"{_taxa_aa_equivalente(ta)*100:.2f}% aa"))
    if linhas_pre:
        for nome, pre in linhas_pre:
            st.caption(f"**{nome}**: ≈ {pre} pré-equivalente")
    else:
        st.caption("Nenhuma classe usa benchmark atrelado a CDI no momento.")
    st.caption("Cálculo aproximado assumindo o CDI (+ ajuste de curva) "
              "constante ao longo do prazo — não é uma curva de mercado "
              "real (DI futuro) importada automaticamente. Use o campo "
              "'Ajuste de curva' acima para calibrar manualmente com dados "
              "da sua mesa, se tiver.")

e = Estrutura(pl_total=pl, classes=classes, taxa_cessao_am=t_ces,
              prazo_medio_meses=prazo_dias / 30, meses_revolvencia=revolv,
              inadimplencia_am=inad, prepagamento_am=prep, recuperacao=recup,
              custos_aa=custos, stress=stress,
              sub_minima=(sub_min_pct / 100) if sub_min_pct > 0 else None,
              meses_rampa=rampa, meses_carencia=carencia,
              custo_inicial=float(custo_inicial or 0),
              custo_inicial_meses=int(custo_inicial_meses or 1),
              prazo_maximo_meses=(int(prazo_maximo)
                                 if prazo_maximo and prazo_maximo > 0
                                 else None))
r = simular(e)
res = r.resumo
pc = r.por_classe
st.session_state["estrutura_atual"] = e

# ------------------------------------------------------------------ métricas
c1, c2, c3, c4 = st.columns(4)
c1.metric("Todas as classes íntegras?",
          "Sim ✅" if res["todas_integras"] else "NÃO ⚠️")
c2.metric("Perdas totais vs júnior", f"{res['perdas_vs_sub']*100:.0f}%")
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

if orig_ativo:
    cs1, cs2 = st.columns([3, 1])
    nome_sim = cs1.text_input(
        "Nome desta simulação",
        value=st.session_state.get("sim_carregada_nome", "") or
        f"Cenário {pd.Timestamp.now().strftime('%d/%m %H:%M')}",
        key="nome_sim_atual")
    if cs2.button("💾 Salvar simulação", width="stretch"):
        params_completos = dict(
            pl=pl, cdi_aa=cdi, ajuste_curva=ajuste_curva,
            t_ces=round(t_ces * 100, 4), modo_cessao=modo_cessao,
            prazo_dias=prazo_dias, revolv=revolv, rampa=rampa,
            carencia=carencia, custo_inicial=custo_inicial,
            custo_inicial_meses=custo_inicial_meses,
            prazo_maximo=prazo_maximo,
            prep=round(prep * 100, 4), inad=round(inad * 100, 4),
            recup=int(recup * 100), custos=round(custos * 100, 4),
            sub_min=sub_min_pct, stress=stress,
            classes_editor=cdf.to_dict(orient="records"),
            classes=[{"nome": c.nome, "pct": c.pct, "taxa_am": c.taxa_am,
                     "residual": c.residual} for c in classes])
        resumo_sim = dict(
            pl_total=pl, subordinacao=pct_sub / 100,
            todas_integras=res["todas_integras"],
            nota_senior=str(ratings.iloc[0]["nota"]) if len(ratings) else None,
            breakeven_senior=be, retorno_sub=res["retorno_sub_multiplo"])
        sim_id_atual = st.session_state.get("sim_carregada_id")
        novo_id = db.salvar_simulacao(orig_ativo, nome_sim, params_completos,
                                      resumo_sim, sim_id=sim_id_atual)
        st.session_state["sim_carregada_id"] = novo_id
        st.session_state["sim_carregada_nome"] = nome_sim
        st.success(f"Simulação '{nome_sim}' salva para "
                  f"{next(o['razao_social'] for o in origs_topo if o['id'] == orig_ativo)}.")

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
    if e.meses_rampa:
        fig.add_vline(x=e.meses_rampa, line_dash="dash", line_color="#999",
                      annotation_text="100% chamado")
    fig.add_vline(x=e.meses_revolvencia, line_dash="dot",
                  annotation_text="fim da revolvência")
    if e.meses_carencia:
        fig.add_vline(x=e.meses_revolvencia + e.meses_carencia,
                      line_dash="dot", line_color="#B33A3A",
                      annotation_text="início da amortização")
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
                          name="Júnior"))
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
                   annotation_text="Júnior inicial")
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
          "demais, ±30%) e mostra o quanto move o retorno da júnior. "
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
        title="Impacto no retorno da júnior (pontos percentuais de "
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
              "júnior: % do aporte)",
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
    ids_aprov = [o["id"] for o in origs]
    idx_aprov = ids_aprov.index(orig_ativo) if orig_ativo in ids_aprov else 0
    orig_sel = ca.selectbox("Originador", ids_aprov, index=idx_aprov,
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
               prazo_dias, revolv, rampa, carencia, round(inad, 6),
               round(stress, 2), round(custo_inicial, 2), custo_inicial_meses,
               prazo_maximo)
    mc_res = st.session_state.get("mc_resultado")
    mc_fp = st.session_state.get("mc_fingerprint")
    mc_stats_memo = mc_res[1] if mc_res and mc_fp == fp_atual else None

    orig_obj = db.obter_originador(orig_sel) or {}
    calib_memo = db.obter_calibracao(orig_sel)

    cc, cd, ce = st.columns(3)
    docx_bytes = gerar_memorando(
        nome_fundo=nome_fundo, originador=orig_obj, estrutura=e,
        resumo=res, por_classe=pc, suporte=sup, calibracao=calib_memo,
        mc_stats=mc_stats_memo, ratings=ratings)
    cc.download_button(
        "📄 Memorando (Word)", data=docx_bytes,
        file_name=f"memorando_{nome_fundo.replace(' ', '_')}.docx",
        mime="application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document",
        width="stretch")

    xlsx_bytes = gerar_excel(
        nome_fundo=nome_fundo, originador=orig_obj, estrutura=e,
        resumo=res, por_classe=pc, fluxo=fluxo, suporte=sup,
        ratings=ratings, mc_stats=mc_stats_memo, calibracao=calib_memo)
    cd.download_button(
        "📊 Fluxo completo (Excel)", data=xlsx_bytes,
        file_name=f"fluxo_{nome_fundo.replace(' ', '_')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet",
        width="stretch")

    if mc_stats_memo is None:
        st.caption("💡 Rode a Simulação de Retornos para esta estrutura "
                  "antes de gerar os arquivos, para incluir a distribuição "
                  "de TIR (Monte Carlo) no memorando e no Excel.")
    if ce.button("Aprovar →", width="stretch",
                 disabled=not res["todas_integras"]):
        params = dict(pl_total=pl, taxa_cessao_am=t_ces,
                      prazo_medio_meses=prazo_dias / 30, meses_revolvencia=revolv,
                      inadimplencia_am=inad, prepagamento_am=prep,
                      recuperacao=recup, custos_aa=custos, cdi_aa=cdi,
                      stress=stress, meses_rampa=rampa, meses_carencia=carencia,
                      sub_minima=(sub_min_pct / 100) if sub_min_pct > 0 else None,
                      custo_inicial=custo_inicial,
                      custo_inicial_meses=custo_inicial_meses,
                      prazo_maximo_meses=(int(prazo_maximo)
                                         if prazo_maximo and prazo_maximo > 0
                                         else None),
                      classes=[{"nome": c.nome, "pct": c.pct,
                                "taxa_am": c.taxa_am,
                                "residual": c.residual} for c in classes],
                      resumo=res)
        deal_id = db.criar_deal(orig_sel, nome_fundo, params)
        db.mover_etapa(orig_sel, "Aprovado")
        params_completos = dict(
            pl=pl, cdi_aa=cdi, ajuste_curva=ajuste_curva,
            t_ces=round(t_ces * 100, 4), modo_cessao=modo_cessao,
            prazo_dias=prazo_dias, revolv=revolv, rampa=rampa,
            carencia=carencia, custo_inicial=custo_inicial,
            custo_inicial_meses=custo_inicial_meses,
            prazo_maximo=prazo_maximo,
            prep=round(prep * 100, 4), inad=round(inad * 100, 4),
            recup=int(recup * 100), custos=round(custos * 100, 4),
            sub_min=sub_min_pct, stress=stress,
            classes_editor=cdf.to_dict(orient="records"),
            classes=params["classes"])
        resumo_sim = dict(
            pl_total=pl, subordinacao=pct_sub / 100, todas_integras=True,
            nota_senior=str(ratings.iloc[0]["nota"]) if len(ratings) else None,
            breakeven_senior=be, retorno_sub=res["retorno_sub_multiplo"])
        sim_id_final = db.salvar_simulacao(
            orig_sel, nome_fundo, params_completos, resumo_sim,
            sim_id=st.session_state.get("sim_carregada_id"))
        db.vincular_deal_simulacao(sim_id_final, deal_id)
        st.session_state["sim_carregada_id"] = sim_id_final
        st.success(f"Deal #{deal_id} criado com os parâmetros desta "
                   "simulação. Acompanhe na Esteira de Constituição.")
    if not res["todas_integras"]:
        st.caption("A aprovação fica bloqueada enquanto houver classe com "
                   "perda no cenário simulado.")
else:
    st.info("Cadastre um originador no funil para vincular a estrutura.")
