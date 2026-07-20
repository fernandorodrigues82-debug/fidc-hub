import json

import streamlit as st

import db

st.set_page_config(page_title="Esteira de constituição", page_icon="🏗️",
                   layout="wide")
db.init_db()

st.title("Esteira de constituição")
st.caption("Cada fundo aprovado vira um deal com checklist por fase — "
           "estruturação, prestadores, registro/oferta e lançamento. "
           "Todo avanço fica na trilha de auditoria.")

deals = db.listar_deals()
if not deals:
    st.info("Nenhum deal aberto. Aprove uma estrutura no Simulador.")
    st.stop()

sel = st.selectbox(
    "Deal", [d["id"] for d in deals],
    format_func=lambda i: next(
        f"#{d['id']} — {d['nome_fundo']} ({d.get('razao_social') or 's/ originador'}) "
        f"— {d['status']}" for d in deals if d["id"] == i))
deal = next(d for d in deals if d["id"] == sel)
checklist = json.loads(deal["checklist"])
params = json.loads(deal["parametros"])

# ------------------------------------------------------------------ progresso
todos = [v for fase in checklist.values() for v in fase.values()]
prog = sum(todos) / len(todos) if todos else 0
st.progress(prog, text=f"Constituição: {prog*100:.0f}% concluída")

c1, c2, c3, c4 = st.columns(4)
c1.metric("PL alvo (R$ mi)", f"{params.get('pl_total', 0)/1e6:,.0f}")
c2.metric("Sênior", f"{params.get('pct_senior', 0)*100:.0f}%")
c3.metric("Subordinação",
          f"{(1 - params.get('pct_senior', 0) - params.get('pct_mezanino', 0))*100:.0f}%")
c4.metric("Revolvência", f"{params.get('meses_revolvencia', 0)} meses")

st.divider()
alterado = False
cols = st.columns(len(checklist))
for col, (fase, itens) in zip(cols, checklist.items()):
    feitos = sum(itens.values())
    with col:
        st.subheader(fase)
        st.caption(f"{feitos}/{len(itens)} itens")
        for item, feito in itens.items():
            novo = st.checkbox(item, value=feito, key=f"{sel}-{fase}-{item}")
            if novo != feito:
                checklist[fase][item] = novo
                alterado = True

if alterado:
    status = "Lançado" if all(
        v for fase in checklist.values() for v in fase.values()) else "Em constituição"
    db.atualizar_checklist(sel, checklist, status)
    st.rerun()

st.divider()
with st.expander("Parâmetros aprovados em comitê (imutáveis no deal)"):
    st.json({k: v for k, v in params.items() if k != "resumo"})
    st.caption("Alterações de estrutura exigem nova simulação e nova aprovação "
               "— o deal guarda a versão que o comitê viu.")
