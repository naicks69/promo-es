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
    # Colunas novas, criadas automaticamente em bancos que já existiam:
    # vendas (exibida no site), preço antigo (para mostrar o desconto) e
    # pontuação (quanto a oferta deve render de comissão, usada para escolher o que vai no WhatsApp)
    colunas_ofertas = colunas_da_tabela("ofertas")
    for coluna, tipo in (("vendas", "INTEGER DEFAULT 0"), ("preco_original", "REAL"), ("pontuacao", "REAL DEFAULT 0")):
        if coluna not in colunas_ofertas:
            executar(f"ALTER TABLE ofertas ADD COLUMN {coluna} {tipo}")

    # 4. Ofertas já enviadas para cada pessoa no WhatsApp
    executar("""
        CREATE TABLE IF NOT EXISTS envios (
            whatsapp_id TEXT,
            produto_id TEXT,
            data_envio TIMESTAMP,
            PRIMARY KEY (whatsapp_id, produto_id)
        )
    """)

    # 5. Grupos de WhatsApp onde o bot posta as ofertas
    executar("""
        CREATE TABLE IF NOT EXISTS grupos (
            jid TEXT PRIMARY KEY,
            nome TEXT,
            link_convite TEXT,
            data_ativacao TIMESTAMP
        )
    """)

    # 7. Quando cada produto foi publicado pela última vez (impede que volte logo depois de sair)
    executar("""
        CREATE TABLE IF NOT EXISTS publicacoes (
            produto_id TEXT PRIMARY KEY,
            titulo TEXT,
            data_publicacao TIMESTAMP
        )
    """)

    # 8. Feedbacks enviados pelos clientes no rodapé do site (o bot repassa no WhatsApp)
    executar("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id TEXT PRIMARY KEY,
            texto TEXT,
            contato TEXT,
            data_criacao TIMESTAMP,
            enviado INTEGER DEFAULT 0
        )
    """)

    # 6. Configurações guardadas pelo próprio sistema (ex.: acesso ao Mercado Livre)
    executar("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            chave TEXT PRIMARY KEY,
            valor TEXT
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

def salvar_oferta_db(id_prod, titulo, preco, link, categoria, loja, imagem="", vendas=0,
                     preco_original=None, pontuacao=0):
    """Grava a oferta nova ou atualiza preço, link e imagem de uma que já está no site.

    A data de publicação só é definida na primeira vez, para que a oferta saia do
    site quando completar o prazo de validade, mesmo que continue aparecendo nas buscas.
    """
    executar("""
        INSERT INTO ofertas (id, titulo, preco, link, categoria, loja, imagem, vendas,
                             preco_original, pontuacao, data_criacao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            titulo = excluded.titulo,
            preco = excluded.preco,
            link = excluded.link,
            categoria = excluded.categoria,
            loja = excluded.loja,
            imagem = excluded.imagem,
            vendas = excluded.vendas,
            preco_original = excluded.preco_original,
            pontuacao = excluded.pontuacao
    """, (id_prod, titulo, preco, link, categoria, loja, imagem, vendas,
          preco_original, pontuacao, agora_utc()))


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
        "preco_original": l[9],
    }


def buscar_ofertas_db(horas_validade=4, limite=400):
    linhas = executar("""
        SELECT id, titulo, preco, link, categoria, loja, imagem, vendas, data_criacao, preco_original
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
        SELECT id, titulo, preco, link, categoria, loja, imagem, vendas, data_criacao, preco_original
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
    # Primeiro as que mais devem render comissão
    sql += " ORDER BY o.pontuacao DESC, o.data_criacao DESC LIMIT ?"
    parametros.append(limite)
    return [linha_para_oferta(l) for l in executar(sql, tuple(parametros), buscar="todos")]


def ultimo_envio(whatsapp_id):
    """Data (texto UTC) da última oferta enviada para a pessoa ou grupo, ou None."""
    linha = executar(
        "SELECT MAX(data_envio) FROM envios WHERE whatsapp_id = ?", (whatsapp_id,), buscar="um"
    )
    return data_iso(linha[0]) if linha and linha[0] else None


def registrar_envio(whatsapp_id, produto_id):
    executar(
        "INSERT INTO envios (whatsapp_id, produto_id, data_envio) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        (whatsapp_id, produto_id, agora_utc()),
    )


# ==========================================
# GRUPOS DE WHATSAPP
# ==========================================

def salvar_grupo(jid, nome, link_convite):
    """Ativa um grupo: ele passa a receber todas as ofertas e o convite aparece no site."""
    executar("""
        INSERT INTO grupos (jid, nome, link_convite, data_ativacao) VALUES (?, ?, ?, ?)
        ON CONFLICT (jid) DO UPDATE SET nome = excluded.nome, link_convite = excluded.link_convite
    """, (jid, nome, link_convite, agora_utc()))
    definir_interesses(jid, ["todas"])


def remover_grupo(jid):
    executar("DELETE FROM grupos WHERE jid = ?", (jid,))
    cancelar_inscricao(jid)


def link_convite_grupo():
    """Link de convite do grupo ativado mais recentemente (exibido no site)."""
    linha = executar("""
        SELECT link_convite FROM grupos
        WHERE link_convite IS NOT NULL AND link_convite != ''
        ORDER BY data_ativacao DESC
        LIMIT 1
    """, buscar="um")
    return linha[0] if linha else ""


# ==========================================
# CONTROLE DE REPETIÇÃO
# ==========================================

def ofertas_ativas(horas_validade=4):
    """[(id, título)] das ofertas que estão no site agora."""
    return executar(
        "SELECT id, titulo FROM ofertas WHERE data_criacao >= ?", (agora_utc(horas_validade),), buscar="todos"
    )


def publicados_desde(horas):
    """[(id, título)] dos produtos publicados no site nas últimas `horas`."""
    return executar(
        "SELECT produto_id, titulo FROM publicacoes WHERE data_publicacao >= ?", (agora_utc(horas),), buscar="todos"
    )


def registrar_publicacao(produto_id, titulo):
    executar("""
        INSERT INTO publicacoes (produto_id, titulo, data_publicacao) VALUES (?, ?, ?)
        ON CONFLICT (produto_id) DO UPDATE SET titulo = excluded.titulo, data_publicacao = excluded.data_publicacao
    """, (produto_id, titulo, agora_utc()))


def limpar_publicacoes_antigas(horas=72):
    executar("DELETE FROM publicacoes WHERE data_publicacao < ?", (agora_utc(horas),))


# ==========================================
# PAINEL DE ADMINISTRAÇÃO
# ==========================================

def contar_ofertas_por_loja(horas_validade=4):
    linhas = executar(
        "SELECT loja, COUNT(*) FROM ofertas WHERE data_criacao >= ? GROUP BY loja",
        (agora_utc(horas_validade),),
        buscar="todos",
    )
    return {loja: total for loja, total in linhas}


def contar_grupos():
    return executar("SELECT COUNT(*) FROM grupos", buscar="um")[0]


# ==========================================
# CONFIGURAÇÕES
# ==========================================

def ler_configuracao(chave, padrao=None):
    linha = executar("SELECT valor FROM configuracoes WHERE chave = ?", (chave,), buscar="um")
    return linha[0] if linha else padrao


def gravar_configuracao(chave, valor):
    executar("""
        INSERT INTO configuracoes (chave, valor) VALUES (?, ?)
        ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor
    """, (chave, valor))


if __name__ == "__main__":
    inicializar_banco()


# ==========================================
# FEEDBACK DOS CLIENTES
# ==========================================

def salvar_feedback(texto, contato=""):
    import uuid
    id_feedback = uuid.uuid4().hex
    executar(
        "INSERT INTO feedbacks (id, texto, contato, data_criacao, enviado) VALUES (?, ?, ?, ?, 0)",
        (id_feedback, texto, contato, agora_utc()),
    )
    return id_feedback


def feedbacks_pendentes(limite=20):
    """Feedbacks que o bot ainda não repassou, do mais antigo para o mais novo."""
    linhas = executar(
        "SELECT id, texto, contato, data_criacao FROM feedbacks WHERE enviado = 0 ORDER BY data_criacao LIMIT ?",
        (limite,),
        buscar="todos",
    )
    return [{"id": l[0], "texto": l[1], "contato": l[2] or "", "data": data_iso(l[3])} for l in linhas]


def marcar_feedbacks_enviados(ids):
    for id_feedback in ids:
        executar("UPDATE feedbacks SET enviado = 1 WHERE id = ?", (str(id_feedback),))


def ultimos_feedbacks(limite=10):
    linhas = executar(
        "SELECT texto, contato, data_criacao, enviado FROM feedbacks ORDER BY data_criacao DESC LIMIT ?",
        (limite,),
        buscar="todos",
    )
    return [{"texto": l[0], "contato": l[1] or "", "data": data_iso(l[2]), "enviado": bool(l[3])} for l in linhas]


def limpar_feedbacks_antigos(dias=90):
    executar("DELETE FROM feedbacks WHERE enviado = 1 AND data_criacao < ?", (agora_utc(dias * 24),))
