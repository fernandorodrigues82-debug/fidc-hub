"""Integração com a curva ETTJ Pré x DI da B3 — a taxa pré "de verdade"
para um prazo específico, em vez de assumir CDI flat com um ajuste manual.

Por que isso importa: a curva de juros brasileira raramente é flat. O CDI
projetado (um número só) mais um "ajuste de curva" digitado à mão é uma
aproximação grosseira — a curva Pré real da B3 tem pontos por prazo
(vértices em dias corridos), e o certo é usar o ponto mais próximo do
prazo médio dos recebíveis sendo descontados (via interpolação), não uma
única taxa aplicada a qualquer prazo.

A B3 não tem uma API REST pública para esse dado especificamente — o que
existe é um arquivo de boletim diário (layout fixed-width, documentado no
Manual de Curvas B3) baixável por URL com a data no nome do arquivo.
Usamos a biblioteca `pyettj` (MIT, mantida, já rastreia a mudança de
endpoint da B3 de dez/2025) em vez de reimplementar esse parser — o
layout por posição de byte é fácil de errar silenciosamente.

Duas camadas de proteção contra a B3 estar fora do ar ou o dia ser feriado:

1. Cache em memória do processo (`st.cache_data`, TTL de 6h).
2. Cache persistido no SQLite (`db.cache_curva_pre`) — sobrevive a restart
   do container e serve de fallback (com aviso) se a busca do dia falhar.

Interpolação: LINEAR entre os dois vértices mais próximos do prazo pedido
(em dias corridos), não a flat-forward-252 "oficial" do Manual de Curvas
B3 — simplificação deliberada. Para os prazos curtos/médios típicos de
FIDC (dias a poucos meses), a diferença entre os dois métodos é pequena;
para prazos longos ou vértices muito espaçados, pode divergir mais.
"""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

CURVA_PADRAO = "PRE"
MAX_DIAS_RETROATIVOS = 10  # cobre feriados prolongados/fins de semana longos


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _buscar_curva_pre_api(data_str: str):
    """Busca a curva PRE completa da B3 para uma data. Levanta exceção em
    qualquer falha -- quem chama decide o fallback. Não cacheia falhas
    (st.cache_data só memoiza retornos bem-sucedidos), então tentar de
    novo a mesma data mais tarde no dia (ex.: arquivo ainda não publicado
    pela manhã) funciona sem precisar limpar cache."""
    import pyettj.ettj as ettj
    df = ettj.get_ettj(data_str, curva=CURVA_PADRAO)
    if df is None or df.empty:
        raise ValueError(f"Sem dados para {data_str} (arquivo vazio — fim "
                         "de semana, feriado, ou pregão ainda não "
                         "publicado)")
    pontos = sorted(
        (int(row["dias_corridos"]), float(row["taxa"]))
        for _, row in df.iterrows()
    )
    refdate = str(df.iloc[0]["refdate"])[:10]
    return pontos, refdate


def _interpolar(pontos: list, dias_alvo: float) -> float:
    """Interpolação linear simples entre os dois vértices mais próximos.
    Fora do intervalo publicado, mantém flat no vértice extremo mais
    próximo (sem extrapolar a inclinação)."""
    if not pontos:
        raise ValueError("Curva sem pontos para interpolar")
    if dias_alvo <= pontos[0][0]:
        return pontos[0][1]
    if dias_alvo >= pontos[-1][0]:
        return pontos[-1][1]
    for (d0, t0), (d1, t1) in zip(pontos, pontos[1:]):
        if d0 <= dias_alvo <= d1:
            if d1 == d0:
                return t0
            peso = (dias_alvo - d0) / (d1 - d0)
            return t0 + peso * (t1 - t0)
    return pontos[-1][1]  # não deveria chegar aqui, dado o sort


def taxa_pre_no_prazo(dias_corridos: float, data: date | None = None) -> dict:
    """Interface pública: busca a curva PRE da B3 (com cache) e devolve a
    taxa interpolada no prazo pedido.

    Caminha para trás dia a dia a partir da data pedida (ou hoje) até achar
    um pregão com dado publicado — cobre o caso óbvio do dia corrente ainda
    não ter arquivo (pregão fecha e o boletim só sai depois), além de fins
    de semana e feriados, sem exigir que quem chama saiba de calendário.

    Sucesso: {'ok': True, 'taxa': float (% a.a.), 'data_curva': str,
              'fonte': 'b3' | 'cache_local', 'aviso': str opcional}
    Falha:   {'ok': False, 'erro': str}
    """
    data_pedida = data or date.today()
    d = data_pedida
    ultimo_erro = None
    for _ in range(MAX_DIAS_RETROATIVOS):
        if d.weekday() < 5:  # só tenta dias úteis (seg=0 ... sex=4)
            try:
                pontos, refdate = _buscar_curva_pre_api(d.strftime("%d/%m/%Y"))
                taxa = _interpolar(pontos, dias_corridos)
                import db
                db.salvar_cache_curva(CURVA_PADRAO, refdate, pontos)
                resultado = {"ok": True, "taxa": taxa, "data_curva": refdate,
                            "fonte": "b3"}
                if d != data_pedida:
                    resultado["aviso"] = (
                        f"Sem pregão em {data_pedida.strftime('%d/%m/%Y')} "
                        f"ainda (fim de semana, feriado, ou boletim do dia "
                        f"não publicado) — usando o último disponível, "
                        f"{refdate}.")
                return resultado
            except Exception as e:  # noqa: BLE001
                ultimo_erro = e
        d -= timedelta(days=1)

    import db
    cache = db.obter_cache_curva(CURVA_PADRAO)
    if cache:
        try:
            taxa = _interpolar(cache["pontos"], dias_corridos)
            return {"ok": True, "taxa": taxa,
                   "data_curva": cache["data_referencia"],
                   "fonte": "cache_local",
                   "aviso": f"B3 indisponível nos últimos "
                           f"{MAX_DIAS_RETROATIVOS} dias úteis "
                           f"({ultimo_erro}); usando última curva salva "
                           f"({cache['atualizado_em']})."}
        except Exception:
            pass
    return {"ok": False,
           "erro": f"Sem pregão disponível nos últimos "
                   f"{MAX_DIAS_RETROATIVOS} dias úteis ({ultimo_erro})"}
