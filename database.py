import os
import sqlite3
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


def inicializar_banco():
    # 1. Tabela de Usuários do WhatsApp
    if not USA_POSTGRES:
        # A versão antiga criava a coluna "telefone", mas o código grava "whatsapp_id",
        # então nenhum cadastro chegou a ser salvo nela. Recria a tabela com a coluna certa.
        colunas = [c[1] for c in executar("PRAGMA table_info(usuarios)", buscar="todos")]
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

    # 4. Histórico de ofertas já disparadas no WhatsApp
    executar("""
        CREATE TABLE IF NOT EXISTS historico_ofertas (
            produto_id TEXT PRIMARY KEY,
            data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Remove os produtos de exemplo antigos (links inventados, que não abriam o produto)
    executar("DELETE FROM ofertas WHERE substr(id, 1, 5) = 'PROD_'")

    banco = "Postgres" if USA_POSTGRES else "SQLite local (os dados somem a cada deploy no Render)"
    print(f"✅ Banco de dados inicializado: {banco}")


# ==========================================
# OFERTAS
# ==========================================

def salvar_oferta_db(id_prod, titulo, preco, link, categoria, loja, imagem=""):
    """Grava a oferta ou atualiza preço, link e imagem se ela já existir."""
    executar("""
        INSERT INTO ofertas (id, titulo, preco, link, categoria, loja, imagem)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            titulo = excluded.titulo,
            preco = excluded.preco,
            link = excluded.link,
            categoria = excluded.categoria,
            loja = excluded.loja,
            imagem = excluded.imagem
    """, (id_prod, titulo, preco, link, categoria, loja, imagem))


def buscar_ofertas_db():
    linhas = executar("""
        SELECT id, titulo, preco, link, categoria, loja, imagem
        FROM ofertas
        WHERE link IS NOT NULL AND link != ''
        ORDER BY data_criacao DESC
        LIMIT 100
    """, buscar="todos")

    ofertas = []
    for l in linhas:
        ofertas.append({
            "id": l[0],
            "titulo": l[1],
            "preco": l[2],
            "link": l[3],
            "categoria": l[4],
            "loja": l[5],
            "imagem": l[6]
        })
    return ofertas


# ==========================================
# WHATSAPP: INTERESSES E HISTÓRICO DE ENVIOS
# ==========================================

def alternar_interesse(whatsapp_id, categoria):
    """Se o usuário já segue a categoria, remove; se não segue, adiciona."""
    executar(
        "INSERT INTO usuarios (whatsapp_id) VALUES (?) ON CONFLICT DO NOTHING", (whatsapp_id,)
    )
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


def usuarios_da_categoria(categoria):
    linhas = executar(
        "SELECT whatsapp_id FROM interesses WHERE categoria = ?", (categoria,), buscar="todos"
    )
    return [l[0] for l in linhas]


def oferta_ja_enviada(produto_id):
    return executar(
        "SELECT 1 FROM historico_ofertas WHERE produto_id = ?", (produto_id,), buscar="um"
    ) is not None


def registrar_envio(produto_id):
    executar(
        "INSERT INTO historico_ofertas (produto_id) VALUES (?) ON CONFLICT DO NOTHING", (produto_id,)
    )


if __name__ == "__main__":
    inicializar_banco()
