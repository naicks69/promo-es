import os
import threading
import time
import requests
from flask import Flask, jsonify, render_template, request
from database import conectar, inicializar_banco, salvar_oferta_db, buscar_ofertas_db
from scraper import buscar_ofertas_afiliados

app = Flask(__name__)
inicializar_banco()

# Endereço do bot de WhatsApp. No Render, configure a variável WHATSAPP_BOT_URL
# com o endereço público do bot (127.0.0.1 só funciona se o bot rodar no mesmo servidor).
NODE_SERVER_URL = os.getenv("WHATSAPP_BOT_URL", "http://127.0.0.1:3000/send-promo")

# Intervalo entre varreduras (segundos) e limite de ofertas novas disparadas por ciclo
INTERVALO_VARREDURA = int(os.getenv("INTERVALO_VARREDURA", "300"))
MAX_ENVIOS_POR_CICLO = int(os.getenv("MAX_ENVIOS_POR_CICLO", "3"))

# Impede que a varredura automática e a /forcar-atualizacao rodem ao mesmo tempo
trava_varredura = threading.Lock()


# ==========================================
# 1. ROTAS DO SITE E DA API DE PRODUTOS
# ==========================================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/ofertas", methods=["GET"])
def api_ofertas():
    # Puxa as ofertas salvas automaticamente do banco de dados
    ofertas = buscar_ofertas_db()
    return jsonify(ofertas)


@app.route("/forcar-atualizacao")
def forcar_atualizacao():
    """Rota manual para forçar a busca e salvar os produtos no banco de dados imediatamente."""
    if not trava_varredura.acquire(blocking=False):
        return "⏳ Já existe uma varredura em andamento. Tente novamente em instantes.", 409
    try:
        total = salvar_ofertas(buscar_ofertas_afiliados())
        return f"🔄 Busca forçada executada com sucesso! O bot encontrou {total} produtos e salvou-os no banco de dados."
    except Exception as e:
        return f"❌ Erro ao forçar atualização: {e}", 500
    finally:
        trava_varredura.release()


# ==========================================
# 2. ROTA DE INTERESSES DO WHATSAPP
# ==========================================

@app.route("/api/interesse", methods=["POST"])
def salvar_interesse():
    dados = request.get_json(silent=True) or {}
    whatsapp_id = dados.get("whatsapp_id")
    categoria = dados.get("categoria")

    if not whatsapp_id or not categoria:
        return jsonify({"status": "erro", "mensagem": "Informe whatsapp_id e categoria"}), 400

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR IGNORE INTO usuarios (whatsapp_id) VALUES (?)", (whatsapp_id,)
    )

    # Alterna o interesse (se já tiver, remove; se não tiver, insere)
    cursor.execute(
        "SELECT * FROM interesses WHERE whatsapp_id = ? AND categoria = ?",
        (whatsapp_id, categoria),
    )
    if cursor.fetchone():
        cursor.execute(
            "DELETE FROM interesses WHERE whatsapp_id = ? AND categoria = ?",
            (whatsapp_id, categoria),
        )
    else:
        cursor.execute(
            "INSERT INTO interesses (whatsapp_id, categoria) VALUES (?, ?)",
            (whatsapp_id, categoria),
        )

    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso"}), 200


# ==========================================
# 3. FUNÇÕES DE AUTOMAÇÃO E WHATSAPP
# ==========================================

def salvar_ofertas(ofertas):
    """Grava no banco todas as ofertas encontradas para o site exibir."""
    for oferta in ofertas:
        salvar_oferta_db(
            oferta["id"],
            oferta["titulo"],
            oferta["preco"],
            oferta["link"],
            oferta["categoria"],
            oferta.get("loja", "shopee"),
            oferta.get("imagem", "")
        )
    return len(ofertas)


def disparar_para_whatsapp(whatsapp_id, texto):
    try:
        requests.post(
            NODE_SERVER_URL,
            json={"numero": whatsapp_id, "mensagem": texto},
            timeout=5,
        )
    except Exception as e:
        print(f"Erro ao disparar mensagem para {whatsapp_id}: {e}")


def enviar_ofertas_whatsapp(ofertas):
    """Envia para o WhatsApp as ofertas que ainda não foram disparadas."""
    conn = conectar()
    cursor = conn.cursor()
    enviadas = 0

    for oferta in ofertas:
        if enviadas >= MAX_ENVIOS_POR_CICLO:
            break  # As demais ficam para o próximo ciclo, evitando excesso de mensagens

        prod_id = oferta["id"]

        # Verifica se a oferta já foi enviada no histórico do WhatsApp
        cursor.execute(
            "SELECT 1 FROM historico_ofertas WHERE produto_id = ?", (prod_id,)
        )
        if cursor.fetchone():
            continue

        # Busca usuários cadastrados nessa categoria
        cursor.execute(
            "SELECT whatsapp_id FROM interesses WHERE categoria = ?",
            (oferta["categoria"],),
        )
        usuarios = cursor.fetchall()

        preco_formatado = f"{oferta['preco']:.2f}".replace(".", ",")
        mensagem = (
            f"🔥 *OFERTA IMPERDÍVEL!* 🔥\n\n"
            f"📦 *{oferta['titulo']}*\n"
            f"💰 Por apenas: *R$ {preco_formatado}*\n\n"
            f"👉 Acesse a promoção: {oferta['link']}"
        )

        for usuario in usuarios:
            wa_id = usuario[0]
            print(f"Enviando oferta para {wa_id}...")
            disparar_para_whatsapp(wa_id, mensagem)
            time.sleep(2)  # Pausa para evitar spam

        # Regista no histórico que o produto já foi disparado
        cursor.execute(
            "INSERT OR IGNORE INTO historico_ofertas (produto_id) VALUES (?)", (prod_id,)
        )
        conn.commit()
        if usuarios:
            enviadas += 1

    conn.close()


def processar_ofertas():
    while True:
        print("\n🔍 Buscando novas ofertas no mercado...")
        with trava_varredura:
            try:
                ofertas = buscar_ofertas_afiliados()

                # 1. Salva automaticamente no banco para o site exibir
                salvar_ofertas(ofertas)

                # 2. Dispara no WhatsApp as ofertas que ainda não foram enviadas
                enviar_ofertas_whatsapp(ofertas)

                print("✅ Ciclo de varredura concluído com sucesso!")
            except Exception as e:
                print(f"Erro no processamento de ofertas: {e}")

        time.sleep(INTERVALO_VARREDURA)


def iniciar_varredura():
    # Inicia a thread de varredura em segundo plano assim que o app carrega,
    # funcionando tanto com "python main.py" quanto com gunicorn
    threading.Thread(target=processar_ofertas, daemon=True).start()


iniciar_varredura()


# ==========================================
# 4. INICIALIZAÇÃO DO SERVIDOR
# ==========================================

if __name__ == "__main__":
    # Pega a porta do ambiente (Render) ou usa a porta 5000 por padrão localmente
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
