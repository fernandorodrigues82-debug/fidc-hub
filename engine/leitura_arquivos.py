"""Leitura robusta de planilhas de carteira/lote de cessão.

Problema comum: exportações do Excel em português usam `;` como separador
(não `,`) e às vezes gravam um BOM (marca de encoding) no início do
arquivo. Sem tratar isso, o cabeçalho inteiro vira uma única coluna e todas
as colunas esperadas parecem "faltando" de uma vez — mesmo estando todas
lá. Esta função tenta várias combinações até achar uma leitura sensata, e
também aceita Excel (.xlsx/.xls) diretamente.
"""

import pandas as pd


def numero_br(serie: pd.Series) -> pd.Series:
    """Converte uma coluna que pode vir em formato brasileiro
    (1.234,56 = mil duzentos e trinta e quatro vírgula cinquenta e seis)
    ou já em formato padrão (1234.56). Detecta pela presença de vírgula:
    só reinterpreta ponto como separador de milhar quando há vírgula —
    caso contrário, deixa o valor como está para o pandas processar."""
    s = serie.astype(str).str.strip()
    tem_virgula = s.str.contains(",", na=False)
    s_convertido = s.where(
        ~tem_virgula,
        s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    return pd.to_numeric(s_convertido, errors="coerce")


def data_br(serie: pd.Series) -> pd.Series:
    """Converte datas aceitando tanto ISO (AAAA-MM-DD, o padrão documentado)
    quanto DD/MM/AAAA (comum em exportações do Excel em português).

    Tenta primeiro SEM dayfirst — necessário para não quebrar o formato
    ISO, que é ambíguo demais para o parser quando forçamos dayfirst (ex.:
    '2026-08-01' vira 8 de janeiro em vez de 1º de agosto se dayfirst=True
    for aplicado cegamente). Só usa dayfirst como fallback se isso claramente
    resolver mais datas do que a tentativa padrão."""
    padrao = pd.to_datetime(serie, errors="coerce")
    if padrao.notna().mean() < 0.7:  # muitas falhas — pode ser formato BR
        alternativa = pd.to_datetime(serie, errors="coerce", dayfirst=True)
        if alternativa.notna().sum() > padrao.notna().sum():
            return alternativa
    return padrao


def ler_planilha(arquivo) -> pd.DataFrame:
    """Recebe o objeto de upload do Streamlit (CSV ou Excel) e devolve um
    DataFrame com nomes de coluna já normalizados (minúsculo, sem espaços
    nas pontas)."""
    nome = (getattr(arquivo, "name", "") or "").lower()

    if nome.endswith((".xlsx", ".xls")):
        df = pd.read_excel(arquivo)
    else:
        df = None
        tentativas = [
            dict(sep=None, engine="python", encoding="utf-8-sig"),
            dict(sep=";", encoding="utf-8-sig"),
            dict(sep=",", encoding="utf-8-sig"),
            dict(sep=None, engine="python"),
        ]
        for kwargs in tentativas:
            try:
                arquivo.seek(0)
                candidato = pd.read_csv(arquivo, **kwargs)
                if candidato.shape[1] > 1:
                    df = candidato
                    break
                if df is None:
                    df = candidato  # guarda alguma leitura, mesmo se ruim
            except Exception:
                continue
        if df is None:
            arquivo.seek(0)
            df = pd.read_csv(arquivo)  # deixa o erro real aparecer

    df.columns = [str(c).strip().lower() for c in df.columns]
    return df
