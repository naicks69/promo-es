import os
import threading
import time
import requests
from flask import Flask, jsonify, render_template, request
from database import conectar, inicializar_banco, salvar_oferta_db, buscar_ofertas_db
from scraper import buscar_ofertas_afiliados

app = Flask(__name__)
inicializar_banco()

NODE_SERVER_URL = "http://127.0.0.1:3000/send-promo"


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
    try:
        ofertas = buscar_ofertas_afiliados()
        
        # Salva cada produto encontrado no banco de dados SQLite
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
            
        return f"🔄 Busca forçada executada com sucesso! O bot encontrou {len(ofertas)} produtos e salvou-os no banco de dados."
    except Exception as e:
        return f"❌ Erro ao forçar atualização: {e}", 500


# ==========================================
# 2. ROTA DE INTERESSES DO WHATSAPP
# ==========================================

@app.route("/api/interesse", methods=["POST"])
def salvar_interesse():
    dados = request.json
    whatsapp_id = dados.get("whatsapp_id")
    categoria = dados.get("categoria")

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

def disparar_para_whatsapp(whatsapp_id, texto):
    try:
        requests.post(
            NODE_SERVER_URL,
            json={"numero": whatsapp_id, "mensagem": texto},
            timeout=5,
        )
    except Exception as e:
        print(f"Erro ao disparar mensagem para {whatsapp_id}: {e}")


def processar_ofertas():
    while True:
        print("\n🔍 Buscando novas ofertas no mercado...")
        try:
            ofertas = buscar_ofertas_afiliados()

            conn = conectar()
            cursor = conn.cursor()

            for oferta in ofertas:
                prod_id = oferta["id"]
                categoria = oferta["categoria"]
                titulo = oferta["titulo"]
                preco = oferta["preco"]
                link = oferta["link"]
                loja = oferta.get("loja", "outros")
                imagem = oferta.get("imagem", "")

                # 1. Salva automaticamente no banco para o site exibir
                salvar_oferta_db(prod_id, titulo, preco, link, categoria, loja, imagem)

                # 2. Verifica se a oferta já foi enviada no histórico do WhatsApp
                cursor.execute(
                    "SELECT * FROM historico_ofertas WHERE produto_id = ?", (prod_id,)
                )
                if cursor.fetchone():
                    continue

                # Busca usuários cadastrados nessa categoria
                cursor.execute(
                    "SELECT whatsapp_id FROM interesses WHERE categoria = ?",
                    (categoria,),
                )
                usuarios = cursor.fetchall()

                mensagem = (
                    f"🔥 *OFERTA IMPERDÍVEL!* 🔥\n\n"
                    f"📦 *{titulo}*\n"
                    f"💰 Por apenas: *R$ {preco}*\n\n"
                    f"👉 Acesse a promoção: {link}"
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

            conn.close()
            print("✅ Ciclo de varredura concluído com sucesso!")
        except Exception as e:
            print(f"Erro no processamento de ofertas: {e}")

        time.sleep(300)  # Aguarda 5 minutos antes da próxima varredura


# ==========================================
# 4. INICIALIZAÇÃO DO SERVIDOR
# ==========================================

if __name__ == "__main__":
    # Inicia a thread de varredura em segundo plano
    threading.Thread(target=processar_ofertas, daemon=True).start()

    # Pega a porta do ambiente (Render) ou usa a porta 5000 por padrão localmente
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)