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


def _ultimo_dia_util_provavel() -> date:
    """Aproximação simples (não usa calendário de feriados): o pregão mais
    recente que provavelmente já fechou. A biblioteca pyettj valida o dia
    de verdade (feriados/fins de semana) e levanta erro se não houver
    pregão -- aqui só evitamos pedir um fim de semana óbvio de cara."""
    d = date.today()
    while d.weekday() >= 5:  # sábado=5, domingo=6
        d -= timedelta(days=1)
    return d


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _buscar_curva_pre_api(data_str: str):
    """Busca a curva PRE completa da B3 para uma data. Levanta exceção em
    qualquer falha -- quem chama decide o fallback."""
    import pyettj.ettj as ettj
    df = ettj.get_ettj(data_str, curva=CURVA_PADRAO)
    if df is None or df.empty:
        raise ValueError("B3 retornou curva vazia para a data")
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

    Sucesso: {'ok': True, 'taxa': float (% a.a.), 'data_curva': str,
              'fonte': 'b3' | 'cache_local'}
    Falha:   {'ok': False, 'erro': str}
    """
    data_alvo = data or _ultimo_dia_util_provavel()
    data_str = data_alvo.strftime("%d/%m/%Y")
    try:
        pontos, refdate = _buscar_curva_pre_api(data_str)
        taxa = _interpolar(pontos, dias_corridos)
        import db
        db.salvar_cache_curva(CURVA_PADRAO, refdate, pontos)
        return {"ok": True, "taxa": taxa, "data_curva": refdate, "fonte": "b3"}
    except Exception as e:  # noqa: BLE001 — rede, parsing, feriado, etc.
        import db
        cache = db.obter_cache_curva(CURVA_PADRAO)
        if cache:
            try:
                taxa = _interpolar(cache["pontos"], dias_corridos)
                return {"ok": True, "taxa": taxa,
                       "data_curva": cache["data_referencia"],
                       "fonte": "cache_local",
                       "aviso": f"B3 indisponível ({e}); usando última "
                               f"curva salva ({cache['atualizado_em']})."}
            except Exception:
                pass
        return {"ok": False, "erro": str(e)}
