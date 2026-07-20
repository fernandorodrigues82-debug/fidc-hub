"""Camada de dados — SQLite local.

Em produção, trocar por Postgres/SQLAlchemy mantendo as mesmas funções.
Todas as escritas registram trilha de auditoria (tabela `auditoria`).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

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
    "score_detalhe",
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
