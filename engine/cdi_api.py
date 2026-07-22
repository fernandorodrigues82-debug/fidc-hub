"""Integração com a API de Séries Temporais do Banco Central (SGS) para
buscar o CDI anualizado (base 252) automaticamente.

Duas camadas de proteção contra a API estar fora do ar ou lenta:

1. Cache em memória do processo (`st.cache_data`, TTL de 6h) — evita repetir
   a chamada de rede a cada rerender do Streamlit dentro da mesma sessão.
2. Cache persistido no SQLite (`db.cache_indices`) — sobrevive a restart do
   container do Streamlit Cloud. Se a API falhar, quem chama pode oferecer
   esse último valor conhecido como fallback antes de recorrer ao valor
   manual digitado pela pessoa.

Nada aqui quebra a página se a rede estiver indisponível: todas as falhas
são capturadas e devolvidas como {'ok': False, 'erro': ...} para a interface
decidir o que mostrar.
"""
from __future__ import annotations

from datetime import datetime

import requests
import streamlit as st

# 4392 = CDI anualizado, base 252 (% a.a.) — já no formato que o simulador usa
# como "CDI projetado (% a.a.)". Séries alternativas do mesmo indicador:
# 12 = CDI diário (% a.d.), 4391 = CDI acumulado no mês.
SERIE_CDI_ANUALIZADO = 4392

URL_SGS = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/"
    "dados/ultimos/{n}?formato=json"
)
TIMEOUT_SEGUNDOS = 6


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _buscar_da_api(serie: int = SERIE_CDI_ANUALIZADO):
    """Busca os últimos registros da série no SGS e devolve (valor, data).

    Levanta exceção em qualquer falha (rede, HTTP, parsing) — a função
    pública `buscar_cdi_atual` decide o que fazer com isso. Pede os últimos
    5 registros (não só o último) porque o dia mais recente às vezes ainda
    não tem valor publicado.
    """
    resp = requests.get(URL_SGS.format(serie=serie, n=5),
                        timeout=TIMEOUT_SEGUNDOS)
    resp.raise_for_status()
    dados = resp.json()
    if not dados:
        raise ValueError("SGS retornou lista vazia")

    validos = [d for d in dados if d.get("valor") not in (None, "")]
    if not validos:
        raise ValueError("Nenhum registro com valor no período consultado")

    ultimo = validos[-1]
    valor = float(str(ultimo["valor"]).replace(",", "."))
    data_ref = datetime.strptime(ultimo["data"], "%d/%m/%Y").date()
    return valor, data_ref


def buscar_cdi_atual() -> dict:
    """Interface pública. Retorna sempre um dict:

    Sucesso: {'ok': True, 'valor': float (% a.a.), 'data_referencia': date}
    Falha:   {'ok': False, 'erro': str}
    """
    try:
        valor, data_ref = _buscar_da_api()
        return {"ok": True, "valor": valor, "data_referencia": data_ref}
    except requests.exceptions.Timeout:
        return {"ok": False, "erro": "Tempo esgotado ao consultar o Bacen "
                                     "(rede lenta ou API fora do ar)."}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "erro": f"Falha de rede ao consultar o Bacen: {e}"}
    except Exception as e:  # noqa: BLE001 — qualquer parsing/formato inesperado
        return {"ok": False, "erro": f"Resposta inesperada do Bacen: {e}"}
