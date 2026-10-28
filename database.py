import sqlite3

# Usaremos um único banco de dados integrado para o site e para o WhatsApp
DB_NAME = "sistema.db"

def conectar():
    return sqlite3.connect(DB_NAME)

def inicializar_banco():
    conn = conectar()
    cursor = conn.cursor()

    # 1. Tabela de Usuários do WhatsApp
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            telefone TEXT PRIMARY KEY
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

    conn.commit()
    conn.close()
    print("✅ Banco de dados unificado inicializado com sucesso!")

def salvar_oferta_db(id_prod, titulo, preco, link, categoria, loja, imagem=""):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO ofertas (id, titulo, preco, link, categoria, loja, imagem)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (id_prod, titulo, preco, link, categoria, loja, imagem))
    conn.commit()
    conn.close()

def buscar_ofertas_db():
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, titulo, preco, link, categoria, loja, imagem 
        FROM ofertas 
        ORDER BY data_criacao DESC 
        LIMIT 50
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