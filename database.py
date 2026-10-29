import sqlite3

# Usaremos um único banco de dados integrado para o site e para o WhatsApp
DB_NAME = "sistema.db"

def conectar():
    # timeout evita erro "database is locked" quando a varredura e o site acessam ao mesmo tempo
    return sqlite3.connect(DB_NAME, timeout=10)

def inicializar_banco():
    conn = conectar()
    cursor = conn.cursor()

    # 1. Tabela de Usuários do WhatsApp
    # A versão antiga criava a coluna "telefone", mas o código grava "whatsapp_id",
    # então nenhum cadastro chegou a ser salvo nela. Recria a tabela com a coluna certa.
    colunas = [c[1] for c in cursor.execute("PRAGMA table_info(usuarios)").fetchall()]
    if colunas and "whatsapp_id" not in colunas:
        cursor.execute("DROP TABLE usuarios")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            whatsapp_id TEXT PRIMARY KEY
        )
    """)

    # 2. Interesses/Preferências de Categoria do WhatsApp
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interesses (
            whatsapp_id TEXT,
            categoria TEXT,
            PRIMARY KEY (whatsapp_id, categoria)
        )
    """)

    # 3. Tabela de Ofertas para o site e histórico
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id TEXT PRIMARY KEY,
            titulo TEXT,
            preco REAL,
            link TEXT,
            categoria TEXT,
            loja TEXT,
            imagem TEXT,
            data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 4. Histórico de ofertas já disparadas no WhatsApp
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_ofertas (
            produto_id TEXT PRIMARY KEY,
            data_envio DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Remove os produtos de exemplo antigos (links inventados, que não abriam o produto)
    cursor.execute("DELETE FROM ofertas WHERE id LIKE 'PROD\\_%' ESCAPE '\\'")

    conn.commit()
    conn.close()
    print("✅ Banco de dados unificado inicializado com sucesso!")

def salvar_oferta_db(id_prod, titulo, preco, link, categoria, loja, imagem=""):
    """Grava a oferta ou atualiza preço, link e imagem se ela já existir."""
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ofertas (id, titulo, preco, link, categoria, loja, imagem)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            titulo = excluded.titulo,
            preco = excluded.preco,
            link = excluded.link,
            categoria = excluded.categoria,
            loja = excluded.loja,
            imagem = excluded.imagem
    """, (id_prod, titulo, preco, link, categoria, loja, imagem))
    conn.commit()
    conn.close()

def buscar_ofertas_db():
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, titulo, preco, link, categoria, loja, imagem
        FROM ofertas
        WHERE link IS NOT NULL AND link != ''
        ORDER BY data_criacao DESC
        LIMIT 100
    """)
    linhas = cursor.fetchall()
    conn.close()

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

if __name__ == "__main__":
    inicializar_banco()
