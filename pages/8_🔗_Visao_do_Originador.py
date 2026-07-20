import json

import pandas as pd
import streamlit as st

import db
from engine.operacao import enquadramento
from engine.rating import classificar_estrutura
from engine.waterfall import estrutura_de_params

st.set_page_config(page_title="Visão do originador", page_icon="🔗",
                   layout="wide")
db.init_db()

st.title("Visão do originador")
st.caption("Um lugar só: cadastro, score, todas as simulações já rodadas "
           "e os fundos vinculados — do primeiro contato até a operação.")

origs = db.listar_originadores()
if not origs:
    st.info("Nenhum originador cadastrado. Comece pelo Funil de Originadores.")
    st.stop()

ids = [o["id"] for o in origs]
idx_default = 0
ativo = st.session_state.get("originador_ativo")
if ativo in ids:
    idx_default = ids.index(ativo)
sel = st.selectbox("Originador", ids, index=idx_default,
                   format_func=lambda i: next(
                       o["razao_social"] for o in origs if o["id"] == i))
st.session_state["originador_ativo"] = sel
o = db.obter_originador(sel)

# ------------------------------------------------------------------ cadastro
c1, c2, c3, c4 = st.columns(4)
c1.metric("Etapa no funil", o["etapa"])
c2.metric("Score", o["score"] if o["score"] is not None else "—")
c3.metric("PL alvo (R$ mi)",
          f"{o['pl_alvo']/1e6:,.0f}" if o.get("pl_alvo") else "—")
c4.metric("Rampa prometida",
          f"{o['meses_rampa']:.0f} m" if o.get("meses_rampa") else "—")
st.caption(f"{o.get('setor') or '—'} · {o.get('tipo_recebivel') or '—'} · "
          f"Volume mensal R$ {(o.get('volume_mensal') or 0)/1e6:,.1f} mi · "
          f"Responsável: {o.get('responsavel') or '—'}")
if o.get("notas"):
    with st.expander("Notas de due diligence"):
        st.write(o["notas"])

calib = db.obter_calibracao(sel)
if calib:
    st.info(f"📊 Carteira calibrada: {calib['n_meses']} meses "
           f"({calib['periodo']}) · volatilidade {calib['vol']} · "
           f"persistência {calib['rho']} · taxa média observada "
           f"{calib['taxa_media_am']*100:.2f}%/mês")

st.divider()

# --------------------------------------------------------------- simulações
st.subheader("Simulações")
sims = db.listar_simulacoes(sel)
if not sims:
    st.caption("Nenhuma simulação salva para este originador ainda. Abra o "
              "Simulador de Estruturação, monte uma estrutura e clique em "
              "'Salvar simulação'.")
else:
    linhas = []
    for s in sims:
        params = json.loads(s["parametros"])
        resumo_cache = json.loads(s["resumo"]) if s["resumo"] else {}
        pl_val = resumo_cache.get("pl_total")
        if pl_val is None:
            pl_val = params.get("pl_total") or params.get("pl", 0)
        linhas.append(dict(
            id=s["id"], nome=s["nome"],
            atualizado=s["atualizado_em"][:16].replace("T", " "),
            pl=pl_val,
            subordinacao=resumo_cache.get("subordinacao"),
            nota_senior=resumo_cache.get("nota_senior") or "—",
            breakeven=resumo_cache.get("breakeven_senior"),
            retorno_sub=resumo_cache.get("retorno_sub"),
            status="✅ Aprovada (fundo)" if s.get("deal_id") else "📝 Rascunho"))
    df_sims = pd.DataFrame(linhas)
    view = df_sims.copy()
    view["PL (R$ mi)"] = (view["pl"] / 1e6).round(1)
    view["Subordinação"] = view["subordinacao"].map(
        lambda v: f"{v*100:.0f}%" if pd.notna(v) else "—")
    view["Break-even sênior"] = view["breakeven"].map(
        lambda v: f"{v:.1f}x" if pd.notna(v) else "—")
    view["Retorno sub"] = view["retorno_sub"].map(
        lambda v: f"{v:.2f}x" if pd.notna(v) else "—")
    st.dataframe(
        view[["nome", "atualizado", "PL (R$ mi)", "Subordinação",
             "nota_senior", "Break-even sênior", "Retorno sub", "status"]]
        .rename(columns={"nome": "Simulação", "atualizado": "Atualizada em",
                         "nota_senior": "Nota sênior", "status": "Status"}),
        hide_index=True, width="stretch")

    cs1, cs2 = st.columns([3, 1])
    sim_abrir = cs1.selectbox(
        "Abrir uma simulação no Simulador de Estruturação",
        [s["id"] for s in sims],
        format_func=lambda i: next(s["nome"] for s in sims if s["id"] == i))
    if cs2.button("🧮 Abrir →", width="stretch"):
        params = json.loads(next(s["parametros"] for s in sims
                                 if s["id"] == sim_abrir))
        params["_sim_id"] = sim_abrir
        params["_sim_nome"] = next(s["nome"] for s in sims
                                   if s["id"] == sim_abrir)
        st.session_state["_carregar_simulacao"] = params
        try:
            st.switch_page("pages/2_🧮_Simulador_de_Estruturacao.py")
        except Exception:
            st.info("Abra a página **Simulador de Estruturação** no menu — "
                    "a simulação selecionada será carregada automaticamente.")

st.divider()

# ------------------------------------------------------------------- fundos
st.subheader("Fundos vinculados")
deals_orig = [d for d in db.listar_deals() if d["originador_id"] == sel]
if not deals_orig:
    st.caption("Nenhuma simulação deste originador foi aprovada em fundo "
              "ainda.")
else:
    for d in deals_orig:
        params = json.loads(d["parametros"])
        with st.container(border=True):
            h1, h2, h3 = st.columns([3, 2, 2])
            h1.markdown(f"**{d['nome_fundo']}** — {d['status']}")
            h2.write(f"PL: R$ {params.get('pl_total', 0)/1e6:,.0f} mi")
            try:
                estrut = estrutura_de_params(params)
                rt = classificar_estrutura(estrut)
                nota = rt.iloc[0]["nota"] if len(rt) else "—"
            except Exception:
                nota = "—"
            h3.write(f"Nota sênior: **{nota}**")

            criterios = db.obter_criterios(d["id"])
            ativa = db.carteira_ativa(d["id"])
            if not ativa.empty:
                enq = enquadramento(ativa, criterios,
                                    pl_referencia=params.get("pl_total", 0))
                e1, e2, e3 = st.columns(3)
                e1.metric("Carteira ativa (R$ mi)",
                          f"{enq['carteira_total']/1e6:,.2f}")
                e2.metric("Maior sacado", f"{enq['maior_sacado_pct']:.1f}%")
                e3.metric("Enquadramento",
                          "✅" if enq["enquadrado"] else "⚠️")
            else:
                st.caption("Ainda sem cessões ingeridas neste fundo.")
            st.caption(f"Deal #{d['id']} · criado em "
                      f"{d['criado_em'][:16].replace('T', ' ')}")

st.caption("Esteira de constituição e operação detalhada continuam nas "
          "páginas próprias — esta tela é o resumo para decidir rápido "
          "o que abrir em seguida.")
