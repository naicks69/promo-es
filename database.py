import os
import sqlite3
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# Com DATABASE_URL configurada (ex.: Postgres gratuito do Neon), os dados ficam
# guardados fora do Render e não se perdem a cada deploy ou reinício.
# Sem ela, usa o arquivo SQLite local (bom para testar no computador).
DATABASE_URL = os.getenv("DATABASE_URL", "")
USA_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_NAME = "sistema.db"


def conectar():
    if USA_POSTGRES:
        import psycopg2
        return psycopg2.connect(DATABASE_URL, connect_timeout=10)
    # timeout evita erro "database is locked" quando a varredura e o site acessam ao mesmo tempo
    return sqlite3.connect(DB_NAME, timeout=10)


def executar(sql, parametros=(), buscar=None):
    """Executa um comando SQL escrito com '?' tanto no SQLite quanto no Postgres.

    buscar: None (só executa), "um" (primeira linha) ou "todos" (todas as linhas).
    """
    if USA_POSTGRES:
        sql = sql.replace("?", "%s")
    conn = conectar()
    try:
        cursor = conn.cursor()
        if USA_POSTGRES and not parametros:
            # Sem parâmetros, o Postgres não tenta interpretar "%" dentro do SQL
            cursor.execute(sql)
        else:
            cursor.execute(sql, parametros)
        if buscar == "um":
            resultado = cursor.fetchone()
        elif buscar == "todos":
            resultado = cursor.fetchall()
        else:
            resultado = None
        conn.commit()
        return resultado
    finally:
        conn.close()


def colunas_da_tabela(tabela):
    if USA_POSTGRES:
        linhas = executar(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (tabela,),
            buscar="todos",
        )
        return [l[0] for l in linhas]
    return [c[1] for c in executar(f"PRAGMA table_info({tabela})", buscar="todos")]


def inicializar_banco():
    # 1. Tabela de Usuários do WhatsApp
    # A versão antiga criava a coluna "telefone", mas o código grava "whatsapp_id",
    # então nenhum cadastro chegou a ser salvo nela. Recria a tabela com a coluna certa.
    colunas = colunas_da_tabela("usuarios")
    if colunas and "whatsapp_id" not in colunas:
        executar("DROP TABLE usuarios")
    executar("""
        CREATE TABLE IF NOT EXISTS usuarios (
            whatsapp_id TEXT PRIMARY KEY
        )
    """)

    # 2. Interesses/Preferências de Categoria do WhatsApp
    executar("""
        CREATE TABLE IF NOT EXISTS interesses (
            whatsapp_id TEXT,
            categoria TEXT,
            PRIMARY KEY (whatsapp_id, categoria)
        )
    """)

    # 3. Tabela de Ofertas para o site
    executar("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id TEXT PRIMARY KEY,
            titulo TEXT,
            preco REAL,
            link TEXT,
            categoria TEXT,
            loja TEXT,
            imagem TEXT,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Quantidade de vendidos na Shopee, exibida no site (coluna nova em bancos já existentes)
    if "vendas" not in colunas_da_tabela("ofertas"):
        executar("ALTER TABLE ofertas ADD COLUMN vendas INTEGER DEFAULT 0")

    # 4. Ofertas já enviadas para cada pessoa no WhatsApp
    executar("""
        CREATE TABLE IF NOT EXISTS envios (
            whatsapp_id TEXT,
            produto_id TEXT,
            data_envio TIMESTAMP,
            PRIMARY KEY (whatsapp_id, produto_id)
        )
    """)

    # Remove os produtos de exemplo antigos (links inventados, que não abriam o produto)
    executar("DELETE FROM ofertas WHERE substr(id, 1, 5) = 'PROD_'")

    banco = "Postgres" if USA_POSTGRES else "SQLite local (os dados somem a cada deploy no Render)"
    print(f"✅ Banco de dados inicializado: {banco}")


# Datas gravadas sempre em UTC, no mesmo formato nos dois bancos
FORMATO_DATA = "%Y-%m-%d %H:%M:%S"


def agora_utc(menos_horas=0):
    momento = datetime.now(timezone.utc) - timedelta(hours=menos_horas)
    return momento.strftime(FORMATO_DATA)


def data_iso(valor):
    """Converte a data vinda do banco (texto no SQLite, datetime no Postgres) para ISO em UTC."""
    if isinstance(valor, datetime):
        valor = valor.strftime(FORMATO_DATA)
    return f"{str(valor)[:19].replace(' ', 'T')}Z" if valor else None


# ==========================================
# OFERTAS
# ==========================================

def salvar_oferta_db(id_prod, titulo, preco, link, categoria, loja, imagem="", vendas=0):
    """Grava a oferta nova ou atualiza preço, link e imagem de uma que já está no site.

    A data de publicação só é definida na primeira vez, para que a oferta saia do
    site quando completar o prazo de validade, mesmo que continue aparecendo nas buscas.
    """
    executar("""
        INSERT INTO ofertas (id, titulo, preco, link, categoria, loja, imagem, vendas, data_criacao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            titulo = excluded.titulo,
            preco = excluded.preco,
            link = excluded.link,
            categoria = excluded.categoria,
            loja = excluded.loja,
            imagem = excluded.imagem,
            vendas = excluded.vendas
    """, (id_prod, titulo, preco, link, categoria, loja, imagem, vendas, agora_utc()))


def remover_ofertas_expiradas(horas_validade):
    """Apaga as ofertas publicadas há mais tempo que o prazo de validade."""
    executar("DELETE FROM ofertas WHERE data_criacao < ?", (agora_utc(horas_validade),))
    # O histórico de envios só precisa existir enquanto a oferta pode voltar a aparecer
    executar("DELETE FROM envios WHERE data_envio < ?", (agora_utc(72),))


def linha_para_oferta(l):
    return {
        "id": l[0],
        "titulo": l[1],
        "preco": l[2],
        "link": l[3],
        "categoria": l[4],
        "loja": l[5],
        "imagem": l[6],
        "vendas": l[7] or 0,
        "publicado_em": data_iso(l[8]),
    }


def buscar_ofertas_db(horas_validade=4, limite=120):
    linhas = executar("""
        SELECT id, titulo, preco, link, categoria, loja, imagem, vendas, data_criacao
        FROM ofertas
        WHERE link IS NOT NULL AND link != '' AND data_criacao >= ?
        ORDER BY data_criacao DESC, vendas DESC
        LIMIT ?
    """, (agora_utc(horas_validade), limite), buscar="todos")
    return [linha_para_oferta(l) for l in linhas]


# ==========================================
# WHATSAPP: INSCRIÇÕES E ENVIOS
# ==========================================

def garantir_usuario(whatsapp_id):
    executar(
        "INSERT INTO usuarios (whatsapp_id) VALUES (?) ON CONFLICT DO NOTHING", (whatsapp_id,)
    )


def alternar_interesse(whatsapp_id, categoria):
    """Se o usuário já segue a categoria, remove; se não segue, adiciona."""
    garantir_usuario(whatsapp_id)
    ja_segue = executar(
        "SELECT 1 FROM interesses WHERE whatsapp_id = ? AND categoria = ?",
        (whatsapp_id, categoria),
        buscar="um",
    )
    if ja_segue:
        executar(
            "DELETE FROM interesses WHERE whatsapp_id = ? AND categoria = ?",
            (whatsapp_id, categoria),
        )
    else:
        executar(
            "INSERT INTO interesses (whatsapp_id, categoria) VALUES (?, ?)",
            (whatsapp_id, categoria),
        )


def definir_interesses(whatsapp_id, categorias):
    """Substitui todas as categorias seguidas pela pessoa ("todas" = recebe de tudo)."""
    garantir_usuario(whatsapp_id)
    executar("DELETE FROM interesses WHERE whatsapp_id = ?", (whatsapp_id,))
    for categoria in categorias:
        executar(
            "INSERT INTO interesses (whatsapp_id, categoria) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (whatsapp_id, categoria),
        )


def cancelar_inscricao(whatsapp_id):
    executar("DELETE FROM interesses WHERE whatsapp_id = ?", (whatsapp_id,))


def listar_inscritos():
    """Devolve {whatsapp_id: [categorias]} de todas as pessoas que recebem ofertas."""
    inscritos = {}
    for whatsapp_id, categoria in executar(
        "SELECT whatsapp_id, categoria FROM interesses", buscar="todos"
    ):
        inscritos.setdefault(whatsapp_id, []).append(categoria)
    return inscritos


def ofertas_para_enviar(whatsapp_id, categorias, limite, horas_validade=4):
    """Ofertas ainda no site, das categorias da pessoa, que ela ainda não recebeu."""
    sql = """
        SELECT id, titulo, preco, link, categoria, loja, imagem, vendas, data_criacao
        FROM ofertas o
        WHERE o.data_criacao >= ?
          AND NOT EXISTS (
              SELECT 1 FROM envios e WHERE e.whatsapp_id = ? AND e.produto_id = o.id
          )
    """
    parametros = [agora_utc(horas_validade), whatsapp_id]
    if "todas" not in categorias:
        sql += f" AND o.categoria IN ({', '.join('?' for _ in categorias)})"
        parametros.extend(categorias)
    sql += " ORDER BY o.data_criacao DESC, o.vendas DESC LIMIT ?"
    parametros.append(limite)
    return [linha_para_oferta(l) for l in executar(sql, tuple(parametros), buscar="todos")]


def registrar_envio(whatsapp_id, produto_id):
    executar(
        "INSERT INTO envios (whatsapp_id, produto_id, data_envio) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        (whatsapp_id, produto_id, agora_utc()),
    )


if __name__ == "__main__":
    inicializar_banco()
