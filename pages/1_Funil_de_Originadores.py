import json

import pandas as pd
import streamlit as st

import db
from engine.score import calcular_score

st.set_page_config(page_title="Funil de originadores", page_icon="📋", layout="wide")
db.init_db()

st.title("Funil de originadores")
st.caption("Prospecção → due diligence → análise de carteira → comitê. "
           "O score de estruturabilidade orienta a decisão e as condições mínimas.")

aba_funil, aba_novo, aba_carteira = st.tabs(
    ["Pipeline", "Cadastrar / editar originador", "Análise de carteira (CSV)"])

# ------------------------------------------------------------------ pipeline
with aba_funil:
    origs = db.listar_originadores()
    if not origs:
        st.info("Nenhum originador cadastrado. Use a aba ao lado.")
    else:
        df = pd.DataFrame(origs)
        df_view = df[["id", "razao_social", "setor", "tipo_recebivel", "etapa",
                      "volume_mensal", "inadimplencia_hist", "concentracao_top10",
                      "score", "responsavel"]].copy()
        df_view["volume_mensal"] = (df_view["volume_mensal"] / 1e6).round(1)
        df_view.columns = ["ID", "Originador", "Setor", "Recebível", "Etapa",
                           "Vol. mensal (R$ mi)", "Inad. hist. (%)",
                           "Conc. top-10 (%)", "Score", "Responsável"]
        st.dataframe(df_view, hide_index=True, width="stretch")

        st.subheader("Mover no funil / decidir")
        c1, c2, c3 = st.columns([2, 2, 1])
        sel = c1.selectbox("Originador",
                           options=[o["id"] for o in origs],
                           format_func=lambda i: next(
                               o["razao_social"] for o in origs if o["id"] == i))
        nova = c2.selectbox("Nova etapa", db.ETAPAS_FUNIL)
        if c3.button("Mover", width="stretch"):
            db.mover_etapa(sel, nova)
            st.success("Etapa atualizada e registrada na auditoria.")
            st.rerun()

        o = db.obter_originador(sel)
        if o and o.get("score_detalhe"):
            st.subheader(f"Score de estruturabilidade — {o['razao_social']}")
            s = calcular_score(o)
            m1, m2, m3 = st.columns(3)
            m1.metric("Score", s["score"])
            m2.metric("Subordinação mínima sugerida",
                      f"{s['subordinacao_minima_sugerida']}%")
            m3.write(f"**Veredicto:** {s['veredicto']}")
            det = pd.DataFrame(s["detalhe"]).T.reset_index()
            det.columns = ["Fator", "Nota (0–100)", "Peso"]
            st.dataframe(det, hide_index=True)
            st.caption("Pesos e réguas em `engine/score.py` — calibrar à política "
                       "de crédito do banco. O detalhamento fica registrado no "
                       "cadastro para a trilha de decisão do comitê.")

# ------------------------------------------------------------------ cadastro
with aba_novo:
    origs = db.listar_originadores()
    modo = st.radio("Modo", ["Novo", "Editar existente"], horizontal=True)
    base = {}
    orig_id = None
    if modo == "Editar existente" and origs:
        orig_id = st.selectbox("Selecionar", [o["id"] for o in origs],
                               format_func=lambda i: next(
                                   o["razao_social"] for o in origs if o["id"] == i))
        base = db.obter_originador(orig_id) or {}

    with st.form("form_orig"):
        c1, c2 = st.columns(2)
        razao = c1.text_input("Razão social *", base.get("razao_social", ""))
        cnpj = c2.text_input("CNPJ", base.get("cnpj", ""))
        setor = c1.text_input("Setor", base.get("setor", ""))
        tipo = c2.selectbox(
            "Tipo de recebível",
            ["Duplicata mercantil", "Cartão de crédito", "Consignado",
             "Recebíveis de convênio", "CT-e / frete", "Contratos / judicial",
             "Outros"],
            index=0 if not base.get("tipo_recebivel") else max(0, [
                "Duplicata mercantil", "Cartão de crédito", "Consignado",
                "Recebíveis de convênio", "CT-e / frete", "Contratos / judicial",
                "Outros"].index(base.get("tipo_recebivel", "Outros"))))
        c3, c4, c5 = st.columns(3)
        vol = c3.number_input("Volume mensal originado (R$)", min_value=0.0,
                              value=float(base.get("volume_mensal") or 5e6),
                              step=5e5, format="%.0f")
        anos = c4.number_input("Anos de operação", min_value=0.0,
                               value=float(base.get("anos_operacao") or 3.0))
        prazo = c5.number_input("Prazo médio (dias)", min_value=1.0,
                                value=float(base.get("prazo_medio_dias") or 60.0))
        c6, c7, c8 = st.columns(3)
        inad = c6.number_input("Inadimplência histórica (%)", min_value=0.0,
                               value=float(base.get("inadimplencia_hist") or 2.0),
                               step=0.1)
        conc = c7.number_input("Concentração top-10 sacados (%)", min_value=0.0,
                               max_value=100.0,
                               value=float(base.get("concentracao_top10") or 30.0))
        resp = c8.text_input("Responsável no banco", base.get("responsavel", ""))
        notas = st.text_area("Notas de due diligence", base.get("notas", ""))

        if st.form_submit_button("Salvar e recalcular score"):
            if not razao:
                st.error("Informe a razão social.")
            else:
                dados = dict(razao_social=razao, cnpj=cnpj, setor=setor,
                             tipo_recebivel=tipo, volume_mensal=vol,
                             anos_operacao=anos, prazo_medio_dias=prazo,
                             inadimplencia_hist=inad, concentracao_top10=conc,
                             responsavel=resp, notas=notas,
                             etapa=base.get("etapa", "Prospecção"))
                s = calcular_score(dados)
                dados["score"] = s["score"]
                dados["score_detalhe"] = json.dumps(s["detalhe"],
                                                    ensure_ascii=False)
                db.salvar_originador(dados, orig_id=orig_id)
                st.success(f"Salvo. Score: {s['score']} — {s['veredicto']} | "
                           f"Subordinação mínima sugerida: "
                           f"{s['subordinacao_minima_sugerida']}%")

# ------------------------------------------------------- análise de carteira
with aba_carteira:
    st.write(
        "Envie a carteira histórica do originador (CSV) para validar os números "
        "declarados. Colunas esperadas: `sacado`, `valor`, `data_vencimento` "
        "(AAAA-MM-DD) e `dias_atraso` (0 = pago em dia; vazio = a vencer)."
    )
    up = st.file_uploader("Carteira (CSV)", type="csv")
    if up:
        try:
            cart = pd.read_csv(up)
            cart.columns = [c.strip().lower() for c in cart.columns]
            obrig = {"sacado", "valor", "data_vencimento"}
            faltam = obrig - set(cart.columns)
            if faltam:
                st.error(f"Colunas ausentes: {', '.join(sorted(faltam))}")
            else:
                cart["valor"] = pd.to_numeric(cart["valor"], errors="coerce")
                cart["data_vencimento"] = pd.to_datetime(
                    cart["data_vencimento"], errors="coerce")
                total = cart["valor"].sum()
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Títulos", f"{len(cart):,}")
                m2.metric("Valor de face (R$ mi)", f"{total/1e6:,.1f}")
                m3.metric("Tíquete médio (R$)",
                          f"{cart['valor'].mean():,.0f}")
                top10 = (cart.groupby("sacado")["valor"].sum()
                         .nlargest(10).sum() / total * 100)
                m4.metric("Concentração top-10", f"{top10:.1f}%")

                if "dias_atraso" in cart.columns:
                    atras = pd.to_numeric(cart["dias_atraso"], errors="coerce")
                    faixas = pd.cut(
                        atras.fillna(-1),
                        bins=[-2, -0.5, 0.5, 30, 60, 90, 10_000],
                        labels=["A vencer", "Em dia", "1–30", "31–60",
                                "61–90", "90+"])
                    aging = (cart.assign(faixa=faixas)
                             .groupby("faixa", observed=False)["valor"].sum()
                             / total * 100).round(2)
                    st.subheader("Aging da carteira (% do valor)")
                    st.bar_chart(aging)
                    inad_90 = float(aging.get("90+", 0))
                    st.metric("Vencidos há mais de 90 dias (proxy de perda)",
                              f"{inad_90:.2f}%")
                    st.caption("Compare com a inadimplência declarada no "
                               "cadastro — divergências relevantes voltam "
                               "para due diligence.")

                st.subheader("Maiores sacados")
                st.dataframe(
                    (cart.groupby("sacado")["valor"].sum().nlargest(15)
                     .reset_index()
                     .assign(pct=lambda d: (d["valor"] / total * 100).round(2))),
                    hide_index=True, width="stretch")
        except Exception as exc:
            st.error(f"Não foi possível ler o arquivo: {exc}")
