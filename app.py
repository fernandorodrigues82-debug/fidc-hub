import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="FIDC Hub", page_icon="🏦", layout="wide")
db.init_db()
db.seed_exemplo()

st.title("FIDC Hub — Estruturação")
st.caption(
    "Plataforma do estruturador: funil de originadores, análise de carteira, "
    "simulação da estrutura e esteira de constituição — com trilha de auditoria."
)

origs = db.listar_originadores()
deals = db.listar_deals()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Originadores no funil",
          len([o for o in origs if o["etapa"] not in ("Aprovado", "Recusado")]))
c2.metric("Aprovados em comitê", len([o for o in origs if o["etapa"] == "Aprovado"]))
c3.metric("Fundos em constituição",
          len([d for d in deals if d["status"] != "Lançado"]))
c4.metric("Volume mensal mapeado (R$ mi)",
          f"{sum(o['volume_mensal'] or 0 for o in origs) / 1e6:,.0f}")

st.divider()
esq, dir_ = st.columns([3, 2])

with esq:
    st.subheader("Funil por etapa")
    if origs:
        df = pd.DataFrame(origs)
        contagem = (df.groupby("etapa").size()
                    .reindex(db.ETAPAS_FUNIL, fill_value=0).reset_index())
        contagem.columns = ["Etapa", "Originadores"]
        st.bar_chart(contagem.set_index("Etapa"))
    st.page_link("pages/1_📋_Funil_de_Originadores.py",
                 label="→ Abrir funil de originadores")
    st.page_link("pages/2_🧮_Simulador_de_Estruturacao.py",
                 label="→ Abrir simulador de estrutura")
    st.page_link("pages/3_🏗️_Esteira_de_Constituicao.py",
                 label="→ Abrir esteira de constituição")
    st.page_link("pages/6_🔄_Operacao_de_Fundos.py",
                 label="→ Abrir operação de fundos")
    st.page_link("pages/4_📖_Guia_de_Conceitos.py",
                 label="→ Abrir guia de conceitos")

with dir_:
    st.subheader("Trilha de auditoria")
    st.caption("Quem fez o quê — base da governança para o acúmulo de papéis "
               "(estruturador + cotista sênior + distribuidor).")
    aud = db.listar_auditoria(15)
    if aud:
        st.dataframe(
            pd.DataFrame(aud)[["quando", "quem", "acao", "entidade", "detalhe"]],
            hide_index=True, width="stretch", height=380,
        )
    else:
        st.info("Nenhum evento registrado ainda.")
