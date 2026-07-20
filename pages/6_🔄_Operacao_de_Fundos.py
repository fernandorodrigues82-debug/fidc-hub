import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import json

import db
from engine.operacao import (alertas_cruzados, calcular_pdd, enquadramento,
                             validar_lote)

st.set_page_config(page_title="Operação de fundos", page_icon="🔄",
                   layout="wide")
db.init_db()

st.title("Operação de fundos")
st.caption("Depois de aprovado, o fundo entra em operação contínua: cada "
           "cessão precisa passar por elegibilidade, a carteira precisa "
           "ficar enquadrada nos limites e a PDD precisa refletir a idade "
           "real dos títulos.")

deals = db.listar_deals()
if not deals:
    st.info("Nenhum fundo aprovado ainda. Aprove uma estrutura no "
            "Simulador de Estruturação primeiro.")
    st.stop()

sel = st.selectbox(
    "Fundo", [d["id"] for d in deals],
    format_func=lambda i: next(
        f"#{d['id']} — {d['nome_fundo']} ({d.get('razao_social') or 's/ originador'}) "
        f"— {d['status']}" for d in deals if d["id"] == i))
deal = next(d for d in deals if d["id"] == sel)
if deal["status"] != "Lançado":
    st.warning("Este fundo ainda não foi marcado como **Lançado** na "
              "Esteira de Constituição — os números abaixo servem para "
              "ensaiar a operação antes do lançamento oficial.")

aba_ingestao, aba_enquadramento, aba_pdd, aba_cruzados, aba_criterios = st.tabs(
    ["📥 Ingestão de cessões", "📐 Enquadramento", "📉 PDD",
     "🔀 Alertas cruzados", "⚙️ Critérios operacionais"])

criterios = db.obter_criterios(sel)
ativa_atual = db.carteira_ativa(sel)
pl_fundo = json.loads(deal["parametros"]).get("pl_total", 0.0)

# --------------------------------------------------------------- critérios
with aba_criterios:
    st.write("Limites operacionais deste fundo — usados para validar cada "
            "nova cessão e para o alerta de enquadramento contínuo.")
    with st.form("form_criterios"):
        c1, c2 = st.columns(2)
        conc_max = c1.number_input(
            "Concentração máxima por sacado (% da carteira)",
            min_value=1.0, max_value=100.0,
            value=float(criterios["concentracao_max_sacado"]), step=1.0)
        prazo_max = c2.number_input(
            "Prazo máximo aceito na cessão (dias)", min_value=1,
            value=int(criterios["prazo_max_dias"]), step=15)
        st.caption("Régua de PDD (dias em atraso → % de provisão sobre o "
                  "valor do título):")
        regua_df = st.data_editor(
            pd.DataFrame(criterios["regua_pdd"],
                        columns=["Dias (mín.)", "Dias (máx.)", "% provisão"]),
            num_rows="fixed", hide_index=True, width="stretch")
        if st.form_submit_button("Salvar critérios", type="primary"):
            novos = dict(concentracao_max_sacado=conc_max,
                        prazo_max_dias=prazo_max,
                        regua_pdd=[tuple(r) for r in regua_df.values.tolist()])
            db.salvar_criterios(sel, novos)
            st.success("Critérios salvos e já valem para a próxima "
                      "ingestão e para o enquadramento.")
            st.rerun()

# --------------------------------------------------------------- ingestão
with aba_ingestao:
    st.write("Envie o lote de novas cessões. Colunas esperadas: `sacado`, "
            "`valor`, `data_vencimento` (AAAA-MM-DD).")
    up = st.file_uploader("Lote de cessão (CSV)", type="csv", key=f"up_{sel}")
    if up:
        try:
            lote = pd.read_csv(up)
            lote.columns = [c.strip().lower() for c in lote.columns]
            faltam = {"sacado", "valor", "data_vencimento"} - set(lote.columns)
            if faltam:
                st.error(f"Colunas ausentes: {', '.join(sorted(faltam))}")
            else:
                lote["valor"] = pd.to_numeric(lote["valor"], errors="coerce")
                carteira_sacado = (ativa_atual.groupby("sacado")["valor"]
                                  .sum().to_dict() if not ativa_atual.empty
                                  else {})
                validado = validar_lote(lote, carteira_sacado, pl_fundo,
                                        criterios)
                n_ok = int(validado["elegivel"].sum())
                v_ok = float(validado.loc[validado["elegivel"], "valor"].sum())
                v_rej = float(validado.loc[~validado["elegivel"], "valor"].sum())

                m1, m2, m3 = st.columns(3)
                m1.metric("Títulos elegíveis", f"{n_ok}/{len(validado)}")
                m2.metric("Valor elegível", f"R$ {v_ok/1e6:,.2f} mi")
                m3.metric("Valor rejeitado", f"R$ {v_rej/1e6:,.2f} mi")

                mostrar = validado.copy()
                mostrar["status"] = mostrar["elegivel"].map(
                    {True: "✅ Elegível", False: "❌ Rejeitado"})
                st.dataframe(
                    mostrar[["sacado", "valor", "data_vencimento", "status",
                            "motivo_inelegivel"]],
                    hide_index=True, width="stretch")

                if v_rej > 0:
                    st.warning(f"{len(validado) - n_ok} título(s) rejeitado(s) "
                              "— não entrarão na carteira do fundo, mas "
                              "ficam registrados na trilha de auditoria "
                              "para consulta.")

                if st.button("Confirmar ingestão do lote", type="primary",
                            disabled=(n_ok == 0)):
                    db.registrar_lote(sel, validado)
                    st.success(f"Lote registrado: {n_ok} títulos "
                              f"(R$ {v_ok/1e6:,.2f} mi) incorporados à "
                              "carteira ativa do fundo.")
                    st.rerun()
        except Exception as exc:
            st.error(f"Não foi possível processar o arquivo: {exc}")

    st.divider()
    lotes = db.listar_lotes(sel)
    if lotes:
        st.subheader("Histórico de lotes")
        df_lotes = pd.DataFrame(lotes)[
            ["id", "criado_em", "n_titulos", "valor_total", "valor_elegivel",
             "valor_rejeitado"]]
        df_lotes.columns = ["Lote", "Data", "Títulos", "Valor total (R$)",
                            "Elegível (R$)", "Rejeitado (R$)"]
        st.dataframe(df_lotes, hide_index=True, width="stretch")
    else:
        st.caption("Nenhum lote ingerido ainda.")

# ----------------------------------------------------------- enquadramento
with aba_enquadramento:
    ativa = db.carteira_ativa(sel)  # recarrega após possível ingestão
    enq = enquadramento(ativa, criterios, pl_referencia=pl_fundo)
    if enq["carteira_total"] == 0:
        st.info("Nenhuma cessão ativa ainda — ingira um lote na aba anterior.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Carteira ativa", f"R$ {enq['carteira_total']/1e6:,.2f} mi")
        c2.metric("Concentração top-10", f"{enq['concentracao_top10']:.1f}%")
        c3.metric("Maior sacado", f"{enq['maior_sacado_pct']:.1f}%",
                  help=enq["maior_sacado_nome"])
        c4.metric("Status", "✅ Enquadrado" if enq["enquadrado"]
                  else "⚠️ Desenquadrado")
        for alerta in enq["alertas"]:
            st.error(alerta)
        st.subheader("Maiores sacados na carteira ativa")
        top = enq["por_sacado"].head(15).reset_index()
        top.columns = ["Sacado", "Valor (R$)"]
        top["% da carteira"] = (top["Valor (R$)"] / enq["carteira_total"]
                                * 100).round(1)
        st.dataframe(top, hide_index=True, width="stretch")
        st.caption(
            "Limite atual: concentração máxima de "
            f"{criterios['concentracao_max_sacado']:.0f}% do PL do fundo "
            "por sacado (ajustável na aba Critérios operacionais). Este é "
            "um "
            "enquadramento de concentração — o acompanhamento de "
            "subordinação viva (saldo real das classes mês a mês) é um "
            "próximo passo natural, hoje coberto apenas na fase de "
            "estruturação (Simulador).")

# -------------------------------------------------------------------- PDD
with aba_pdd:
    ativa = db.carteira_ativa(sel)
    pdd = calcular_pdd(ativa, criterios["regua_pdd"])
    if pdd["total_carteira"] == 0:
        st.info("Nenhuma cessão ativa ainda — ingira um lote na aba de "
                "ingestão.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Carteira ativa", f"R$ {pdd['total_carteira']/1e6:,.2f} mi")
        c2.metric("PDD total", f"R$ {pdd['total_pdd']/1e6:,.2f} mi")
        c3.metric("PDD / carteira", f"{pdd['pct_pdd']*100:.2f}%")
        fig = go.Figure(go.Bar(x=pdd["breakdown"].index,
                               y=pdd["breakdown"].values / 1e6,
                               marker_color="#0B5563"))
        fig.update_layout(title="Carteira ativa por faixa de atraso (R$ mi)",
                          xaxis_title="Dias em atraso", height=340)
        st.plotly_chart(fig, width="stretch")
        st.caption("A idade de cada título é calculada dinamicamente a "
                  "partir da data de vencimento — a PDD se atualiza "
                  "sozinha a cada vez que esta página é aberta, sem "
                  "precisar de novo upload.")

# ----------------------------------------------------------- alertas cruzados
with aba_cruzados:
    st.write("Sacados presentes em mais de um fundo do banco — risco de "
            "concentração que nenhum enquadramento individual enxerga.")
    todos_ativos = db.carteira_ativa_todos_deals()
    deals_meta = {d["id"]: d for d in deals}
    ac = alertas_cruzados(todos_ativos, deals_meta)
    if ac.empty:
        st.info("Nenhum sacado repetido entre fundos com carteira ativa, "
                "até o momento.")
    else:
        ac_view = ac.copy()
        ac_view["valor_total"] = (ac_view["valor_total"] / 1e6).round(2)
        ac_view.columns = ["Sacado", "Exposição total (R$ mi)", "Nº fundos",
                          "Fundos"]
        st.dataframe(ac_view, hide_index=True, width="stretch")
        st.caption("Revise se a exposição consolidada desse sacado, somada "
                  "entre os fundos, é compatível com o apetite de risco do "
                  "banco como um todo — mesmo que cada fundo individual "
                  "esteja enquadrado.")
