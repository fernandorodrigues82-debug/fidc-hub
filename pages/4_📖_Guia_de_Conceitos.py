import streamlit as st

from engine.conceitos import CONCEITOS

st.set_page_config(page_title="Guia de conceitos", page_icon="📖", layout="wide")

st.title("Guia de conceitos")
st.caption("O que cada parâmetro significa, faixas típicas de mercado e como "
           "ele mexe na estrutura. Os mesmos textos aparecem no ícone (?) ao "
           "lado de cada campo do simulador e do cadastro.")

GRUPOS = {
    "Estrutura de capital": ["pl_total", "senior", "mezanino", "subordinada"],
    "Taxas e retorno": ["taxa_cessao", "taxa_senior", "taxa_mezanino", "tir"],
    "Carteira e prazos": ["prazo_medio", "revolvencia", "rampa", "carencia", "prepagamento"],
    "Risco e proteção": ["inadimplencia", "recuperacao", "custos", "stress", "sub_minima"],
}

busca = st.text_input("Buscar conceito", placeholder="ex.: subordinação, break-even, cessão")

for grupo, chaves in GRUPOS.items():
    itens = []
    for ch in chaves:
        c = CONCEITOS[ch]
        texto = (c["titulo"] + c["resumo"] + c["faixa"] + c["efeito"]).lower()
        if not busca or busca.lower() in texto:
            itens.append(c)
    if not itens:
        continue
    st.subheader(grupo)
    for c in itens:
        with st.expander(c["titulo"]):
            st.write(c["resumo"])
            st.markdown(f"**Faixa típica de mercado:** {c['faixa']}")
            st.markdown(f"**Efeito na estrutura:** {c['efeito']}")

st.divider()
st.caption("Faixas são referências de mercado para orientar a primeira "
           "calibração — cada carteira exige validação com dados reais "
           "(módulo de análise de carteira) e aprovação do comitê.")
