"""Camada de dados — SQLite local.

Em produção, trocar por Postgres/SQLAlchemy mantendo as mesmas funções.
Todas as escritas registram trilha de auditoria (tabela `auditoria`).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "fidc_hub.db"

# ---------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS originadores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    razao_social TEXT NOT NULL,
    cnpj TEXT,
    setor TEXT,
    tipo_recebivel TEXT,             -- duplicata, cartao, consignado, judicial...
    volume_mensal REAL,              -- R$ originados/mes
    anos_operacao REAL,
    inadimplencia_hist REAL,         -- % perda liquida da carteira historica
    concentracao_top10 REAL,         -- % dos 10 maiores sacados
    prazo_medio_dias REAL,
    etapa TEXT DEFAULT 'Prospecção', -- funil
    responsavel TEXT,
    notas TEXT,
    score REAL,
    score_detalhe TEXT,              -- json com decomposicao
    criado_em TEXT,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    originador_id INTEGER REFERENCES originadores(id),
    nome_fundo TEXT NOT NULL,
    status TEXT DEFAULT 'Estruturação',
    parametros TEXT,                 -- json: estrutura simulada aprovada
    checklist TEXT,                  -- json: esteira de constituicao
    criado_em TEXT,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS calibracoes (
    originador_id INTEGER PRIMARY KEY REFERENCES originadores(id),
    taxa_media_am REAL,
    vol REAL,
    rho REAL,
    n_meses INTEGER,
    periodo TEXT,
    serie TEXT,               -- json da série mensal (para exibir depois)
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS lotes_cessao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER REFERENCES deals(id),
    n_titulos INTEGER,
    valor_total REAL,
    valor_elegivel REAL,
    valor_rejeitado REAL,
    criado_em TEXT
);

CREATE TABLE IF NOT EXISTS cessoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER REFERENCES deals(id),
    lote_id INTEGER REFERENCES lotes_cessao(id),
    sacado TEXT,
    valor REAL,
    data_vencimento TEXT,
    elegivel INTEGER,
    motivo_inelegivel TEXT,
    criado_em TEXT
);

CREATE TABLE IF NOT EXISTS simulacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    originador_id INTEGER REFERENCES originadores(id),
    deal_id INTEGER REFERENCES deals(id),
    nome TEXT,
    parametros TEXT,          -- json completo (inclui a tabela de classes
                              -- no formato do editor, para recarregar fiel)
    resumo TEXT,              -- json com metricas-chave para listagem rapida
    criado_em TEXT,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quando TEXT,
    quem TEXT,
    acao TEXT,
    entidade TEXT,
    entidade_id INTEGER,
    detalhe TEXT
);
"""

ETAPAS_FUNIL = [
    "Prospecção",
    "Due diligence",
    "Análise de carteira",
    "Simulação / Comitê",
    "Aprovado",
    "Recusado",
]

CHECKLIST_PADRAO = {
    "Estruturação": [
        "Termos aprovados em comitê (subordinação, benchmark, prazo)",
        "Minuta do regulamento (classes e anexos)",
        "Minuta do contrato de cessão e condições de cessão",
        "Definição de critérios de elegibilidade",
    ],
    "Prestadores": [
        "Administrador fiduciário contratado",
        "Gestor contratado (responsabilidade sobre lastro — RCVM 175)",
        "Custodiante / controlador contratado",
        "Agente de cobrança definido",
        "Auditor independente contratado",
        "Registradora de recebíveis integrada",
        "Rating contratado (se aplicável)",
    ],
    "Registro e oferta": [
        "Registro do fundo na CVM (via administrador)",
        "Enquadramento da oferta — RCVM 160 (se pública)",
        "Suitability do público-alvo — RCVM 30",
        "Material de distribuição aprovado por compliance",
        "Mapeamento de conflitos de interesse (banco sênior + distribuidor)",
    ],
    "Lançamento": [
        "Bookbuilding / alocação concluída",
        "Integralização das cotas",
        "Primeira cessão liquidada com verificação de lastro",
    ],
}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
        for ddl in ("ALTER TABLE deals ADD COLUMN criterios_operacionais TEXT",
                   "ALTER TABLE originadores ADD COLUMN pl_alvo REAL",
                   "ALTER TABLE originadores ADD COLUMN meses_rampa REAL"):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # coluna já existe


def _audit(c, quem, acao, entidade, entidade_id, detalhe=""):
    c.execute(
        "INSERT INTO auditoria (quando, quem, acao, entidade, entidade_id, detalhe) "
        "VALUES (?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), quem, acao, entidade,
         entidade_id, detalhe),
    )


# ------------------------------------------------------------ originadores

CAMPOS_ORIG = [
    "razao_social", "cnpj", "setor", "tipo_recebivel", "volume_mensal",
    "anos_operacao", "inadimplencia_hist", "concentracao_top10",
    "prazo_medio_dias", "etapa", "responsavel", "notas", "score",
    "score_detalhe", "pl_alvo", "meses_rampa",
]


def salvar_originador(dados: dict, quem: str = "usuário", orig_id: int | None = None):
    agora = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        if orig_id:
            sets = ", ".join(f"{k}=?" for k in CAMPOS_ORIG)
            c.execute(
                f"UPDATE originadores SET {sets}, atualizado_em=? WHERE id=?",
                [dados.get(k) for k in CAMPOS_ORIG] + [agora, orig_id],
            )
            _audit(c, quem, "atualizou", "originador", orig_id,
                   json.dumps(dados, ensure_ascii=False, default=str))
            return orig_id
        cols = ", ".join(CAMPOS_ORIG)
        marks = ", ".join("?" for _ in CAMPOS_ORIG)
        cur = c.execute(
            f"INSERT INTO originadores ({cols}, criado_em, atualizado_em) "
            f"VALUES ({marks}, ?, ?)",
            [dados.get(k) for k in CAMPOS_ORIG] + [agora, agora],
        )
        _audit(c, quem, "criou", "originador", cur.lastrowid,
               dados.get("razao_social", ""))
        return cur.lastrowid


def listar_originadores():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM originadores ORDER BY atualizado_em DESC")]


def obter_originador(orig_id: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM originadores WHERE id=?", (orig_id,)).fetchone()
        return dict(r) if r else None


def mover_etapa(orig_id: int, etapa: str, quem: str = "usuário"):
    with _conn() as c:
        c.execute(
            "UPDATE originadores SET etapa=?, atualizado_em=? WHERE id=?",
            (etapa, datetime.now().isoformat(timespec="seconds"), orig_id),
        )
        _audit(c, quem, "moveu etapa", "originador", orig_id, etapa)


def contar_vinculos_originador(orig_id: int) -> dict:
    """Quantos deals e simulações referenciam este originador — para
    avisar antes de uma exclusão."""
    with _conn() as c:
        n_deals = c.execute(
            "SELECT COUNT(*) FROM deals WHERE originador_id=?",
            (orig_id,)).fetchone()[0]
        n_sims = c.execute(
            "SELECT COUNT(*) FROM simulacoes WHERE originador_id=?",
            (orig_id,)).fetchone()[0]
        n_calib = c.execute(
            "SELECT COUNT(*) FROM calibracoes WHERE originador_id=?",
            (orig_id,)).fetchone()[0]
    return dict(deals=n_deals, simulacoes=n_sims, calibracoes=n_calib)


def excluir_originador(orig_id: int, quem: str = "usuário"):
    """Remove o originador e suas simulações/calibrações. NÃO remove deals
    já aprovados (ficam órfãos de originador, preservando o histórico do
    fundo) — o chamador deve avisar o usuário se houver deals vinculados."""
    with _conn() as c:
        nome = c.execute("SELECT razao_social FROM originadores WHERE id=?",
                         (orig_id,)).fetchone()
        c.execute("DELETE FROM simulacoes WHERE originador_id=?", (orig_id,))
        c.execute("DELETE FROM calibracoes WHERE originador_id=?", (orig_id,))
        c.execute("DELETE FROM originadores WHERE id=?", (orig_id,))
        _audit(c, quem, "excluiu originador", "originador", orig_id,
              nome[0] if nome else "")


# ---------------------------------------------------------------- deals

def criar_deal(originador_id: int, nome_fundo: str, parametros: dict,
               quem: str = "usuário"):
    agora = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        checklist = {
            fase: {item: False for item in itens}
            for fase, itens in CHECKLIST_PADRAO.items()
        }
        cur = c.execute(
            "INSERT INTO deals (originador_id, nome_fundo, parametros, checklist, "
            "criado_em, atualizado_em) VALUES (?,?,?,?,?,?)",
            (originador_id, nome_fundo,
             json.dumps(parametros, ensure_ascii=False, default=str),
             json.dumps(checklist, ensure_ascii=False), agora, agora),
        )
        _audit(c, quem, "criou", "deal", cur.lastrowid, nome_fundo)
        return cur.lastrowid


def listar_deals():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT d.*, o.razao_social FROM deals d "
            "LEFT JOIN originadores o ON o.id = d.originador_id "
            "ORDER BY d.atualizado_em DESC")]


def atualizar_checklist(deal_id: int, checklist: dict, status: str,
                        quem: str = "usuário"):
    with _conn() as c:
        c.execute(
            "UPDATE deals SET checklist=?, status=?, atualizado_em=? WHERE id=?",
            (json.dumps(checklist, ensure_ascii=False), status,
             datetime.now().isoformat(timespec="seconds"), deal_id),
        )
        _audit(c, quem, "atualizou checklist", "deal", deal_id, status)


def listar_auditoria(limite: int = 200):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))]


# ------------------------------------------------------------ calibrações

def salvar_calibracao(originador_id: int, calib, quem: str = "usuário"):
    agora = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "INSERT INTO calibracoes (originador_id, taxa_media_am, vol, "
            "rho, n_meses, periodo, serie, atualizado_em) VALUES "
            "(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(originador_id) DO UPDATE SET "
            "taxa_media_am=excluded.taxa_media_am, vol=excluded.vol, "
            "rho=excluded.rho, n_meses=excluded.n_meses, "
            "periodo=excluded.periodo, serie=excluded.serie, "
            "atualizado_em=excluded.atualizado_em",
            (originador_id, calib.taxa_media_am, calib.vol, calib.rho,
             calib.n_meses, calib.periodo,
             calib.serie.to_json(orient="records"), agora),
        )
        _audit(c, quem, "calibrou carteira", "originador", originador_id,
               f"vol={calib.vol} rho={calib.rho} n_meses={calib.n_meses}")


def obter_calibracao(originador_id: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM calibracoes WHERE originador_id=?",
                      (originador_id,)).fetchone()
        return dict(r) if r else None


# ------------------------------------------------------------ simulações

def salvar_simulacao(originador_id: int, nome: str, parametros: dict,
                     resumo: dict, sim_id: int | None = None,
                     quem: str = "usuário") -> int:
    agora = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        if sim_id:
            c.execute(
                "UPDATE simulacoes SET nome=?, parametros=?, resumo=?, "
                "atualizado_em=? WHERE id=?",
                (nome, json.dumps(parametros, ensure_ascii=False, default=str),
                 json.dumps(resumo, ensure_ascii=False, default=str), agora,
                 sim_id))
            _audit(c, quem, "atualizou simulação", "simulacao", sim_id, nome)
            return sim_id
        cur = c.execute(
            "INSERT INTO simulacoes (originador_id, nome, parametros, "
            "resumo, criado_em, atualizado_em) VALUES (?,?,?,?,?,?)",
            (originador_id, nome,
             json.dumps(parametros, ensure_ascii=False, default=str),
             json.dumps(resumo, ensure_ascii=False, default=str),
             agora, agora))
        _audit(c, quem, "salvou simulação", "simulacao", cur.lastrowid, nome)
        return cur.lastrowid


def listar_simulacoes(originador_id: int | None = None):
    with _conn() as c:
        if originador_id:
            rows = c.execute(
                "SELECT s.*, o.razao_social FROM simulacoes s "
                "LEFT JOIN originadores o ON o.id = s.originador_id "
                "WHERE s.originador_id=? ORDER BY s.atualizado_em DESC",
                (originador_id,)).fetchall()
        else:
            rows = c.execute(
                "SELECT s.*, o.razao_social FROM simulacoes s "
                "LEFT JOIN originadores o ON o.id = s.originador_id "
                "ORDER BY s.atualizado_em DESC").fetchall()
        return [dict(r) for r in rows]


def obter_simulacao(sim_id: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM simulacoes WHERE id=?",
                      (sim_id,)).fetchone()
        return dict(r) if r else None


def vincular_deal_simulacao(sim_id: int, deal_id: int, quem: str = "usuário"):
    with _conn() as c:
        c.execute("UPDATE simulacoes SET deal_id=? WHERE id=?",
                  (deal_id, sim_id))
        _audit(c, quem, "vinculou simulação ao fundo aprovado", "simulacao",
              sim_id, f"deal_id={deal_id}")


def excluir_simulacao(sim_id: int, quem: str = "usuário"):
    with _conn() as c:
        c.execute("DELETE FROM simulacoes WHERE id=?", (sim_id,))
        _audit(c, quem, "excluiu simulação", "simulacao", sim_id, "")


# ------------------------------------------------------------- operação

def obter_criterios(deal_id: int) -> dict:
    from engine.operacao import DEFAULT_CRITERIOS
    with _conn() as c:
        r = c.execute("SELECT criterios_operacionais FROM deals WHERE id=?",
                      (deal_id,)).fetchone()
    if r and r["criterios_operacionais"]:
        return {**DEFAULT_CRITERIOS, **json.loads(r["criterios_operacionais"])}
    return dict(DEFAULT_CRITERIOS)


def salvar_criterios(deal_id: int, criterios: dict, quem: str = "usuário"):
    with _conn() as c:
        c.execute("UPDATE deals SET criterios_operacionais=? WHERE id=?",
                  (json.dumps(criterios, ensure_ascii=False), deal_id))
        _audit(c, quem, "atualizou critérios operacionais", "deal", deal_id,
              json.dumps(criterios, ensure_ascii=False))


def registrar_lote(deal_id: int, df_validado, quem: str = "usuário") -> int:
    """Grava o lote e os títulos (elegíveis e rejeitados, para trilha)."""
    agora = datetime.now().isoformat(timespec="seconds")
    elegiveis = df_validado[df_validado["elegivel"]]
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO lotes_cessao (deal_id, n_titulos, valor_total, "
            "valor_elegivel, valor_rejeitado, criado_em) VALUES (?,?,?,?,?,?)",
            (deal_id, len(df_validado), float(df_validado["valor"].sum()),
             float(elegiveis["valor"].sum()),
             float(df_validado.loc[~df_validado["elegivel"], "valor"].sum()),
             agora))
        lote_id = cur.lastrowid
        for _, row in df_validado.iterrows():
            c.execute(
                "INSERT INTO cessoes (deal_id, lote_id, sacado, valor, "
                "data_vencimento, elegivel, motivo_inelegivel, criado_em) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (deal_id, lote_id, row["sacado"], float(row["valor"]),
                 str(row["data_vencimento"].date())
                 if pd.notna(row["data_vencimento"]) else None,
                 int(row["elegivel"]), row["motivo_inelegivel"], agora))
        _audit(c, quem, "ingeriu lote de cessão", "deal", deal_id,
              f"{len(df_validado)} títulos, "
              f"R$ {elegiveis['valor'].sum():,.0f} elegíveis")
    return lote_id


def listar_lotes(deal_id: int):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM lotes_cessao WHERE deal_id=? ORDER BY id DESC",
            (deal_id,))]


def carteira_ativa(deal_id: int):
    """Todas as cessões elegíveis já ingeridas para o fundo (DataFrame)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT sacado, valor, data_vencimento FROM cessoes "
            "WHERE deal_id=? AND elegivel=1", (deal_id,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows],
                        columns=["sacado", "valor", "data_vencimento"])


def carteira_ativa_todos_deals():
    """{deal_id: DataFrame} com a carteira ativa de todos os fundos —
    para os alertas cruzados de concentração de sacado entre fundos."""
    with _conn() as c:
        rows = c.execute(
            "SELECT deal_id, sacado, valor, data_vencimento FROM cessoes "
            "WHERE elegivel=1").fetchall()
    df = pd.DataFrame([dict(r) for r in rows],
                      columns=["deal_id", "sacado", "valor",
                              "data_vencimento"])
    return {deal_id: g.drop(columns="deal_id")
           for deal_id, g in df.groupby("deal_id")} if not df.empty else {}


# ---------------------------------------------------------------- seed

def seed_exemplo():
    """Carrega dados de exemplo se o banco estiver vazio."""
    if listar_originadores():
        return
    exemplos = [
        dict(razao_social="AgroPag Soluções S.A.", cnpj="12.345.678/0001-90",
             setor="Agronegócio", tipo_recebivel="Duplicata mercantil",
             volume_mensal=18_000_000, anos_operacao=7,
             inadimplencia_hist=1.8, concentracao_top10=32,
             prazo_medio_dias=75, etapa="Análise de carteira",
             responsavel="M. Costa", notas="Carteira pulverizada no interior de GO."),
        dict(razao_social="MedCred Antecipações Ltda.", cnpj="98.765.432/0001-10",
             setor="Saúde", tipo_recebivel="Recebíveis de convênio",
             volume_mensal=9_500_000, anos_operacao=4,
             inadimplencia_hist=0.9, concentracao_top10=61,
             prazo_medio_dias=90, etapa="Due diligence",
             responsavel="R. Lima", notas="Concentração alta em 3 operadoras."),
        dict(razao_social="LogFrete Tech S.A.", cnpj="45.678.912/0001-55",
             setor="Logística", tipo_recebivel="CT-e / frete",
             volume_mensal=27_000_000, anos_operacao=9,
             inadimplencia_hist=3.4, concentracao_top10=18,
             prazo_medio_dias=45, etapa="Prospecção",
             responsavel="M. Costa", notas="Inadimplência acima do setor; negociar subordinação maior."),
    ]
    from engine.score import calcular_score
    for e in exemplos:
        s = calcular_score(e)
        e["score"], e["score_detalhe"] = s["score"], json.dumps(s["detalhe"],
                                                               ensure_ascii=False)
        salvar_originador(e, quem="seed")
