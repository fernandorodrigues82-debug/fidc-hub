"""Modelagem de perdas em cenário único ("bullet") para apresentação em
comitê — mesmo conceito da planilha de referência usada pelo time (PL
Inicial -> Rentabilidade -> Perdas -> Custos -> Remuneração -> PL Final,
com o PL Final distribuído pela cascata Sênior -> Mezanino -> Júnior),
recalibrado com duas correções em relação ao original:

1. CDI não é mais contado em dobro. A planilha original guardava o custo
   já cheio (CDI + spread) e depois multiplicava por (1+CDI) de novo na
   hora de calcular a remuneração -- isso inflava a remuneração de Sênior
   e Mezanino em ~2x e, por tabela, deixava os limiares de perda de cada
   cenário otimistas demais. Aqui CDI e spread entram como grandezas
   separadas (mesma convenção do Simulador de Estruturação) e a
   remuneração é a taxa cheia (CDI+spread) composta uma única vez.

2. Período explícito. Em vez de misturar uma "CDI do período" solta com
   taxas "(aa)", o prazo do fundo é um parâmetro explícito (em meses) e
   toda taxa anual é composta pro prazo real -- deixa de depender de
   presumir (sem declarar) que o período é sempre 12 meses.

O modelo continua sendo de UM ÚNICO PERÍODO (bullet): a carteira é cedida
uma vez, rende a taxa de cessão o prazo inteiro, e o PL Final resultante é
distribuído de uma vez pela cascata -- não é o motor de amortização mês a
mês do Simulador de Estruturação (`engine/waterfall.py`). É o modelo certo
para a pergunta "quanto de perda a estrutura aguenta", não para desenhar o
cronograma de pagamento.

SEGMENTAÇÃO POR POOL DE RISCO
------------------------------
Carteiras concentradas costumam ter perfis de risco bem diferentes dentro
do mesmo fundo -- por exemplo, uma fatia pulverizada em sacados de alta
qualidade (risco sacado) e outra fatia de risco do próprio originador
(às vezes com coobrigação). Tratar isso como uma taxa de perda única e
homogênea mascara o risco de cada fatia. `PoolRisco` permite declarar N
fatias da carteira de DCs, cada uma com sua própria perda-base, número de
contrapartes (para choque idiossincrático) e sensibilidade a um fator
sistêmico comum (para o cenário correlacionado). O modo de carteira única
(um `perda_base_pct` só) continua funcionando -- pools é aditivo, não uma
migração obrigatória.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class PoolRisco:
    """Uma fatia da carteira de direitos creditórios com perfil de risco
    próprio, dentro do mesmo fundo."""
    nome: str
    pct_carteira: float          # fração da carteira de DCs (não do PL)
    tipo_risco: str = "sacado"   # "sacado" | "originador"
    regime_juridico: str = "true_sale"  # "true_sale" | "coobrigacao"
    perda_base_pct: float = 0.005       # perda assumida no cenário Base, DESSE pool
    n_contrapartes: int | None = None   # p/ choque idiossincrático (nome único)
    rating: str | None = None           # ex.: "AAA", "AA", "Sem rating"
    correlacao_macro: float = 0.5       # sensibilidade a um fator sistêmico comum (0-1)

    @property
    def risco_dobrado(self) -> bool:
        """Coobrigação só funciona como segunda linha de defesa se o
        coobrigado for uma parte DIFERENTE do devedor original. Quando o
        risco já é do próprio originador E a cessão tem recurso a ele
        mesmo, não há segunda linha -- é o mesmo nome duas vezes."""
        return self.tipo_risco == "originador" and self.regime_juridico == "coobrigacao"


@dataclass
class ParametrosBullet:
    pl_inicial: float
    pct_senior: float          # fração do PL, ex.: 0.75
    pct_mezanino: float        # fração do PL, ex.: 0.10 (júnior = resíduo)
    pct_caixa: float           # fração do PL alocada em caixa (não em DCs)
    prazo_meses: float         # horizonte único modelado (o "bullet")
    taxa_cessao_am: float      # taxa de cessão sobre a carteira, ao mês
    cdi_aa: float              # CDI projetado, % a.a. (ex.: 0.14)
    ajuste_curva_aa: float = 0.0   # prêmio/desconto sobre o CDI projetado
    spread_senior_aa: float = 0.02     # SÓ o spread, sem embutir CDI
    spread_mezanino_aa: float = 0.03   # idem
    custo_fidc_aa: float = 0.0135      # taxa de administração/custos, % PL a.a.
    perda_base_pct: float = 0.005      # perda assumida no cenário Base (modo carteira única)
    pools: list[PoolRisco] = field(default_factory=list)  # opcional: segmentação por risco

    @property
    def pct_junior(self) -> float:
        return max(0.0, 1 - self.pct_senior - self.pct_mezanino)

    @property
    def cota_senior(self) -> float:
        return self.pl_inicial * self.pct_senior

    @property
    def cota_mezanino(self) -> float:
        return self.pl_inicial * self.pct_mezanino

    @property
    def cota_junior(self) -> float:
        return self.pl_inicial * self.pct_junior

    @property
    def cdi_efetivo_aa(self) -> float:
        """CDI projetado + ajuste de curva -- mesma convenção do Simulador."""
        return self.cdi_aa + self.ajuste_curva_aa

    @property
    def carteira_dcs(self) -> float:
        return self.pl_inicial * (1 - self.pct_caixa)

    def _compor(self, taxa_aa: float) -> float:
        """Composição de uma taxa anual pelo prazo real do fundo (em vez de
        presumir 12 meses)."""
        return (1 + taxa_aa) ** (self.prazo_meses / 12) - 1

    @property
    def taxa_cessao_periodo(self) -> float:
        """Taxa de cessão (a.m.) composta pelo prazo inteiro, em meses."""
        return (1 + self.taxa_cessao_am) ** self.prazo_meses - 1

    @property
    def cdi_periodo(self) -> float:
        return self._compor(self.cdi_efetivo_aa)

    @property
    def custo_senior_aa(self) -> float:
        return self.cdi_efetivo_aa + self.spread_senior_aa

    @property
    def custo_mezanino_aa(self) -> float:
        return self.cdi_efetivo_aa + self.spread_mezanino_aa

    @property
    def remuneracao_senior(self) -> float:
        """Remuneração contratual da Sênior no período -- taxa cheia
        (CDI+spread) composta UMA vez pelo prazo, sem multiplicar o CDI de
        novo. Fixa: não depende da perda (é subtraída antes da cascata,
        que depois aloca o que sobrar pelo valor de face das cotas)."""
        return self._compor(self.custo_senior_aa) * self.cota_senior

    @property
    def remuneracao_mezanino(self) -> float:
        return self._compor(self.custo_mezanino_aa) * self.cota_mezanino

    @property
    def remuneracao_total(self) -> float:
        return self.remuneracao_senior + self.remuneracao_mezanino

    @property
    def custo_fundo(self) -> float:
        """Taxa de administração/custos do FIDC -- pro-rata linear pelo
        prazo (convenção comum para taxas de administração; não composta)."""
        return self.custo_fidc_aa * (self.prazo_meses / 12) * self.pl_inicial


def _montar_resultado(p: ParametrosBullet, perdas_valor: float, **extra) -> dict:
    """Núcleo compartilhado: dado o valor de perda em R$ (já ponderado, seja
    de um perda_pct único ou de uma soma por pool), monta rentabilidade, PL
    Final e a cascata Sr/Meza/Jr. Usado tanto pelo modo carteira única
    quanto pelo modo por pool -- a cascata é idêntica nos dois casos."""
    perda_pct_consolidada = perdas_valor / p.carteira_dcs if p.carteira_dcs else 0.0
    rentabilidade = ((p.carteira_dcs - perdas_valor) * p.taxa_cessao_periodo
                     + p.pl_inicial * p.pct_caixa * p.cdi_periodo)
    pl_final = (p.pl_inicial + rentabilidade - perdas_valor - p.custo_fundo
               - p.remuneracao_total)

    sr_final = min(p.cota_senior, max(0.0, pl_final))
    meza_final = min(p.cota_mezanino, max(0.0, pl_final - p.cota_senior))
    jr_final = max(0.0, pl_final - p.cota_senior - p.cota_mezanino)

    return {
        "perda_pct": perda_pct_consolidada,
        "perdas_valor": perdas_valor,
        "rentabilidade": rentabilidade,
        "custo_fundo": p.custo_fundo,
        "remuneracao_total": p.remuneracao_total,
        "pl_final": pl_final,
        "sr_final": sr_final,
        "meza_final": meza_final,
        "jr_final": jr_final,
        "pct_sr": (sr_final / p.cota_senior) if p.cota_senior else None,
        "pct_meza": (meza_final / p.cota_mezanino) if p.cota_mezanino else None,
        "pct_jr": (jr_final / p.cota_junior) if p.cota_junior else None,
        "retorno_jr": (jr_final / p.cota_junior - 1) if p.cota_junior else None,
        **extra,
    }


def calcular_cenario(p: ParametrosBullet, perda_pct: float) -> dict:
    """Calcula todas as linhas do modelo para uma taxa de perda dada
    (perda_pct: fração da carteira de DCs perdida ao longo do período).
    Modo carteira única -- não considera pools."""
    return _montar_resultado(p, perda_pct * p.carteira_dcs)


def calcular_cenario_pools(p: ParametrosBullet,
                           perdas_por_pool: dict[str, float]) -> dict:
    """Igual a calcular_cenario, mas cada pool perde na sua própria taxa
    (perdas_por_pool: {nome_do_pool: perda_pct_do_pool}). A perda em R$ é a
    soma ponderada pelo tamanho de cada pool -- o resto do modelo (custo do
    fundo, remuneração, cascata) é o mesmo, porque essas linhas dependem do
    PL/cotas totais, não da composição interna da carteira."""
    perdas_valor = sum(
        pool.pct_carteira * p.carteira_dcs * perdas_por_pool.get(pool.nome, 0.0)
        for pool in p.pools
    )
    detalhe_pools = {
        pool.nome: {
            "perda_pct": perdas_por_pool.get(pool.nome, 0.0),
            "perda_valor": pool.pct_carteira * p.carteira_dcs
                          * perdas_por_pool.get(pool.nome, 0.0),
            "valor_pool": pool.pct_carteira * p.carteira_dcs,
        }
        for pool in p.pools
    }
    return _montar_resultado(p, perdas_valor, detalhe_pools=detalhe_pools)


def perda_para_pl_final(p: ParametrosBullet, pl_final_alvo: float) -> float | None:
    """Resolve algebricamente (fórmula fechada) a taxa de perda sobre a
    carteira de DCs necessária para que o PL Final atinja um valor-alvo.

    PL Final é LINEAR em perda_pct (dado o restante fixo), então a solução
    é direta -- sem precisar de busca iterativa (Goal Seek) como a
    planilha original fazia manualmente antes de ter a fórmula fechada.
    Retorna None se a carteira de DCs for zero (sem perda possível)."""
    if p.carteira_dcs == 0:
        return None
    constante = (p.pl_inicial + p.carteira_dcs * p.taxa_cessao_periodo
                + p.pl_inicial * p.pct_caixa * p.cdi_periodo
                - p.custo_fundo - p.remuneracao_total)
    denom = p.carteira_dcs * (1 + p.taxa_cessao_periodo)
    return (constante - pl_final_alvo) / denom


def multiplicador_para_pl_final(p: ParametrosBullet,
                                perdas_base_por_pool: dict[str, float],
                                pl_final_alvo: float) -> float | None:
    """Generaliza perda_para_pl_final para o modo pools: em vez de resolver
    uma taxa de perda única, resolve um MULTIPLICADOR DE SEVERIDADE k tal
    que escalando a perda-base de cada pool por k (perda_pool = k *
    perdas_base_por_pool[nome]) o PL Final atinge o alvo.

    Ainda é uma solução fechada (linear): perdas_valor(k) = k * soma dos
    valores-base por pool, então PL_final(k) também é linear em k.
    Retorna None se a soma das perdas-base for zero (não dá pra escalar
    algo que já é zero para atingir um alvo diferente do caso k=0)."""
    if p.carteira_dcs == 0:
        return None
    perdas_valor_base = sum(
        pool.pct_carteira * p.carteira_dcs * perdas_base_por_pool.get(pool.nome, 0.0)
        for pool in p.pools
    )
    if perdas_valor_base == 0:
        return None
    constante = (p.pl_inicial + p.carteira_dcs * p.taxa_cessao_periodo
                + p.pl_inicial * p.pct_caixa * p.cdi_periodo
                - p.custo_fundo - p.remuneracao_total)
    # PL_final(k) = constante - k*perdas_valor_base*(1+taxa_cessao_periodo)
    denom = perdas_valor_base * (1 + p.taxa_cessao_periodo)
    return (constante - pl_final_alvo) / denom


def pontos_de_ruptura_pools(p: ParametrosBullet,
                            perdas_base_por_pool: dict[str, float]) -> list[dict]:
    """Equivalente a pontos_de_ruptura, mas para o modo pools: em vez de uma
    perda % única, reporta um MULTIPLICADOR DE SEVERIDADE sobre as
    perdas-base combinadas de todos os pools -- ex.: 'a estrutura aguenta
    até 2,3x a perda-base combinada antes de zerar a Júnior'. Cada cenário
    também detalha a perda % resultante em CADA pool nesse multiplicador."""
    cenarios_alvo = [
        ("Base", "Perda-base de cada pool, sem estresse adicional", 1.0),
    ]

    alvo_pess = p.cota_senior + p.cota_mezanino + p.cota_junior
    k_pess = multiplicador_para_pl_final(p, perdas_base_por_pool, alvo_pess)
    cenarios_alvo.append((
        "Pessimista",
        "Multiplicador que zera o retorno da Júnior", k_pess))

    alvo_stress = p.cota_senior + p.cota_mezanino
    k_stress = multiplicador_para_pl_final(p, perdas_base_por_pool, alvo_stress)
    cenarios_alvo.append((
        "Stress", "Multiplicador que zera a Júnior por completo", k_stress))

    alvo_catastrofe = p.cota_senior
    k_catastrofe = multiplicador_para_pl_final(p, perdas_base_por_pool, alvo_catastrofe)
    cenarios_alvo.append((
        "Catástrofe",
        "Multiplicador que zera a Mezanino — Sênior no limiar", k_catastrofe))

    resultado = []
    for nome, descricao, k in cenarios_alvo:
        if k is None:
            continue
        perdas_pool_k = {n: min(1.0, max(0.0, k * v))
                        for n, v in perdas_base_por_pool.items()}
        r = calcular_cenario_pools(p, perdas_pool_k)
        resultado.append({"nome": nome, "descricao": descricao,
                          "multiplicador": k, **r})
    return resultado


def choque_idiossincratico(p: ParametrosBullet,
                           perdas_base_por_pool: dict[str, float],
                           pool_nome: str, lgd: float = 1.0) -> dict | None:
    """Cenário 'quebra do maior nome': a maior contraparte de UM pool
    (assumindo nomes de peso aproximadamente igual dentro do pool) deixa de
    pagar por completo. Os demais pools seguem na perda-base. Conservador
    por design (lgd=100% default) -- é um teste de concentração, não uma
    previsão de perda esperada. Retorna None se o pool não tiver
    n_contrapartes definido (não dá pra estimar o peso de 1 nome)."""
    pool = next((x for x in p.pools if x.nome == pool_nome), None)
    if pool is None or not pool.n_contrapartes:
        return None
    perda_pool_choque = min(1.0, lgd / pool.n_contrapartes)
    perdas = dict(perdas_base_por_pool)
    perdas[pool_nome] = max(perdas.get(pool_nome, 0.0), perda_pool_choque)
    r = calcular_cenario_pools(p, perdas)
    r["descricao"] = (f"1 de {pool.n_contrapartes} contrapartes de "
                      f"'{pool_nome}' deixa de pagar por completo "
                      f"(demais pools na perda-base)")
    return r


def cenario_sistemico(p: ParametrosBullet,
                      perdas_base_por_pool: dict[str, float],
                      choque_macro: float) -> dict:
    """Choque sistêmico simplificado: um fator comum atinge todos os pools
    ao mesmo tempo, cada um na proporção da sua correlacao_macro --
    perda_pool_estressada = perda_base_pool * (1 + choque_macro *
    correlacao_macro_pool). É uma aproximação estilizada de correlação (um
    fator, não uma cópula completa) -- serve para mostrar que pools com
    correlação alta ao mesmo fator sistêmico pioram JUNTOS, não são
    independentes como o resto do modelo assume por padrão."""
    perdas = {
        pool.nome: min(1.0, perdas_base_por_pool.get(pool.nome, 0.0)
                      * (1 + choque_macro * pool.correlacao_macro))
        for pool in p.pools
    }
    r = calcular_cenario_pools(p, perdas)
    r["descricao"] = (f"Choque sistêmico de {choque_macro*100:.0f}% "
                      "aplicado a todos os pools, ponderado pela "
                      "correlação de cada um ao fator comum")
    return r


def pontos_de_ruptura(p: ParametrosBullet) -> list[dict]:
    """Os 4 cenários nomeados: Base (perda assumida) + os 3 limiares que a
    estrutura atual sofre, na ordem de severidade. Generalizado por
    recovery-rate, não hardcoded por coluna como na planilha original --
    se a estrutura mudar (%s das classes), os limiares recalculam certo."""
    cenarios = [
        ("Base", "Perda assumida/histórica", p.perda_base_pct),
    ]

    alvo_pessimista = p.cota_senior + p.cota_mezanino + p.cota_junior
    perda_pess = perda_para_pl_final(p, alvo_pessimista)
    cenarios.append((
        "Pessimista",
        "Perda que zera o retorno da Júnior (ela recebe só o capital de volta)",
        perda_pess,
    ))

    alvo_stress = p.cota_senior + p.cota_mezanino
    perda_stress = perda_para_pl_final(p, alvo_stress)
    cenarios.append((
        "Stress",
        "Perda que zera a Júnior por completo",
        perda_stress,
    ))

    alvo_catastrofe = p.cota_senior
    perda_catastrofe = perda_para_pl_final(p, alvo_catastrofe)
    cenarios.append((
        "Catástrofe",
        "Perda que zera a Mezanino — a Sênior está no limiar de ser afetada",
        perda_catastrofe,
    ))

    resultado = []
    for nome, descricao, perda in cenarios:
        if perda is None:
            continue
        r = calcular_cenario(p, perda)
        resultado.append({"nome": nome, "descricao": descricao, **r})
    return resultado


def curva_perda(p: ParametrosBullet, passos: int = 101) -> pd.DataFrame:
    """Curva contínua de perda (0% a 100% da carteira) x recuperação por
    classe -- mostra de forma suave onde cada ruptura acontece, em vez de
    só os 4 pontos discretos dos cenários nomeados."""
    perdas = np.linspace(0, 1, passos)
    linhas = [calcular_cenario(p, x) for x in perdas]
    return pd.DataFrame(linhas)


def curva_multiplicador_pools(p: ParametrosBullet,
                              perdas_base_por_pool: dict[str, float],
                              k_max: float = 5.0, passos: int = 101) -> pd.DataFrame:
    """Equivalente a curva_perda para o modo pools: varia o multiplicador
    de severidade k de 0 a k_max, escalando a perda-base de todos os pools
    junto, e mostra a recuperação de cada classe -- mesma leitura da curva
    de carteira única, só que no eixo de severidade em vez de perda %."""
    ks = np.linspace(0, k_max, passos)
    linhas = []
    for k in ks:
        perdas_k = {n: min(1.0, k * v) for n, v in perdas_base_por_pool.items()}
        r = calcular_cenario_pools(p, perdas_k)
        r["multiplicador"] = k
        linhas.append(r)
    return pd.DataFrame(linhas)
