"""Interpreta a descrição livre de uma operação e extrai parâmetros.

Dois níveis:
1. `interpretar_local` — regras/regex em pt-BR, roda sem dependências.
2. `interpretar_llm` — se houver ANTHROPIC_API_KEY em st.secrets, usa a API
   da Anthropic para uma extração muito mais robusta (e, no futuro, voz
   transcrita cai no mesmo funil).

Sempre retorna: (parametros: dict, entendimentos: list[str])
Chaves possíveis: pl_total, pct_senior, pct_mezanino, cdi_aa, spread_senior,
spread_mezanino, taxa_cessao_am, prazo_medio_meses, meses_revolvencia,
inadimplencia_am_pct.
"""

import json
import re

NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(".", "").replace(",", ".")) if ("," in s and "." in s) \
        else float(s.replace(",", "."))


def interpretar_local(texto: str):
    t = " " + texto.lower().replace("\n", " ") + " "
    p, log = {}, []

    # PL: "R$ 100 milhões", "fundo de 80 mi", "PL de 50mm"
    m = re.search(rf"(?:pl|fidc|fundo|patrim[oô]nio|opera[cç][aã]o)\D{{0,20}}r?\$?\s*{NUM}\s*(mm|mi(?:lh[oõ]es|lh[aã]o)?|bi(?:lh[oõ]es|lh[aã]o)?)", t)
    if not m:
        m = re.search(rf"r\$\s*{NUM}\s*(mm|mi(?:lh[oõ]es|lh[aã]o)?|bi(?:lh[oõ]es|lh[aã]o)?)", t)
    if m:
        mult = 1e9 if m.group(2).startswith("bi") else 1e6
        p["pl_total"] = _f(m.group(1)) * mult
        log.append(f"PL de R$ {p['pl_total']/1e6:,.0f} mi")

    # percentuais por classe
    m = re.search(rf"s[eê]nior[^\d,;.]{{0,15}}{NUM}\s*%", t) or \
        re.search(rf"{NUM}\s*%\s*(?:de\s+)?s[eê]nior", t)
    if m:
        p["pct_senior"] = _f(m.group(1)) / 100
        log.append(f"sênior {m.group(1)}%")
    m = re.search(rf"mezanino[^\d,;.]{{0,15}}{NUM}\s*%", t) or \
        re.search(rf"{NUM}\s*%\s*(?:de\s+)?mezanino", t)
    if m:
        p["pct_mezanino"] = _f(m.group(1)) / 100
        log.append(f"mezanino {m.group(1)}%")
    m = re.search(rf"subordina(?:da|[cç][aã]o)[^\d,;.]{{0,15}}{NUM}\s*%", t)
    if m and "pct_senior" not in p:
        sub = _f(m.group(1)) / 100
        p["pct_senior"] = round(1 - sub - p.get("pct_mezanino", 0.0), 4)
        log.append(f"subordinação {m.group(1)}% (sênior derivada: "
                   f"{p['pct_senior']*100:.0f}%)")

    # CDI e spreads: "sênior CDI + 3", "CDI+2,5% aa no mezanino"
    m = re.search(rf"cdi\s*(?:de|a|em)?\s*{NUM}\s*%", t)
    if m:
        p["cdi_aa"] = _f(m.group(1))
        log.append(f"CDI projetado {m.group(1)}% a.a.")
    # spreads "CDI + X": a classe normalmente precede o spread
    # ("sênior a CDI+3"); só usa a classe seguinte como fallback
    for sp in re.finditer(rf"cdi\s*\+\s*{NUM}", t):
        antes = [cm for cm in re.finditer(r"s[eê]nior|mezanino", t)
                 if cm.end() <= sp.start() and sp.start() - cm.end() <= 40]
        depois = [cm for cm in re.finditer(r"s[eê]nior|mezanino", t)
                  if cm.start() >= sp.end() and cm.start() - sp.end() <= 40]
        alvo = antes[-1].group(0) if antes else (
            depois[0].group(0) if depois else None)
        if alvo:
            chave = "spread_senior" if "nior" in alvo else "spread_mezanino"
            if chave not in p:
                p[chave] = _f(sp.group(1))
                log.append(f"{'sênior' if 'nior' in alvo else 'mezanino'} "
                           f"CDI + {sp.group(1)}% a.a.")

    # taxa de cessão a.m.
    m = re.search(rf"cess[aã]o\D{{0,20}}{NUM}\s*%\s*(?:a\.?m\.?|ao m[eê]s)?", t)
    if m:
        p["taxa_cessao_am"] = _f(m.group(1)) / 100
        log.append(f"cessão {m.group(1)}% a.m.")

    # prazo médio: dias ou meses
    m = re.search(rf"prazo\s*(?:m[eé]dio)?\D{{0,10}}{NUM}\s*dias", t)
    if m:
        p["prazo_medio_meses"] = max(1, round(_f(m.group(1)) / 30))
        log.append(f"prazo médio {m.group(1)} dias (~{p['prazo_medio_meses']} m)")
    else:
        m = re.search(rf"prazo\s*(?:m[eé]dio)?\D{{0,10}}{NUM}\s*m[eê]s", t)
        if m:
            p["prazo_medio_meses"] = max(1, round(_f(m.group(1))))
            log.append(f"prazo médio {m.group(1)} meses")

    # revolvência: meses ou anos
    m = re.search(rf"revolv[eê]ncia\D{{0,10}}{NUM}\s*anos?", t)
    if m:
        p["meses_revolvencia"] = int(_f(m.group(1)) * 12)
        log.append(f"revolvência {m.group(1)} anos")
    else:
        m = re.search(rf"revolv[eê]ncia\D{{0,10}}{NUM}\s*m", t)
        if m:
            p["meses_revolvencia"] = int(_f(m.group(1)))
            log.append(f"revolvência {m.group(1)} meses")

    # inadimplência / perda
    m = re.search(rf"(?:inadimpl[eê]ncia|perda)\D{{0,25}}{NUM}\s*%", t)
    if m:
        p["inadimplencia_am_pct"] = _f(m.group(1))
        log.append(f"inadimplência {m.group(1)}%")

    return p, log


PROMPT_LLM = """Extraia parâmetros de estruturação de FIDC do texto do usuário.
Responda APENAS um JSON (sem markdown) com as chaves que conseguir extrair:
pl_total (número em R$), pct_senior (0-1), pct_mezanino (0-1), cdi_aa (% a.a.),
spread_senior (% a.a.), spread_mezanino (% a.a.), taxa_cessao_am (0-1 a.m.),
prazo_medio_meses (int), meses_revolvencia (int), inadimplencia_am_pct (%).
Se o texto der subordinação, derive pct_senior = 1 - sub - mezanino."""


def interpretar_llm(texto: str, api_key: str):
    """Extração via API da Anthropic. Levanta exceção em falha de rede."""
    import requests
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
              "system": PROMPT_LLM,
              "messages": [{"role": "user", "content": texto}]},
        timeout=30,
    )
    resp.raise_for_status()
    bruto = "".join(b.get("text", "") for b in resp.json()["content"])
    p = json.loads(re.sub(r"```(?:json)?|```", "", bruto).strip())
    p = {k: v for k, v in p.items() if v is not None}
    log = [f"{k} = {v}" for k, v in p.items()]
    return p, log
