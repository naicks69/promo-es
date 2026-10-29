import os
import threading
import time
import requests
from flask import Flask, jsonify, render_template, request
from database import (
    inicializar_banco,
    salvar_oferta_db,
    buscar_ofertas_db,
    alternar_interesse,
    usuarios_da_categoria,
    oferta_ja_enviada,
    registrar_envio,
)
from scraper import buscar_ofertas_afiliados

app = Flask(__name__)
inicializar_banco()

# Endereço do bot de WhatsApp. No Render, configure a variável WHATSAPP_BOT_URL
# com o endereço público do bot (127.0.0.1 só funciona se o bot rodar no mesmo servidor).
NODE_SERVER_URL = os.getenv("WHATSAPP_BOT_URL", "http://127.0.0.1:3000/send-promo")

# Link de convite do grupo/canal de WhatsApp exibido no site (ex.: https://chat.whatsapp.com/...)
LINK_GRUPO_WHATSAPP = os.getenv("WHATSAPP_LINK", "")

# Intervalo entre varreduras (segundos) e limite de ofertas novas disparadas por ciclo
INTERVALO_VARREDURA = int(os.getenv("INTERVALO_VARREDURA", "300"))
MAX_ENVIOS_POR_CICLO = int(os.getenv("MAX_ENVIOS_POR_CICLO", "3"))

# Endereço público do próprio site. O Render preenche RENDER_EXTERNAL_URL sozinho.
URL_PUBLICA = os.getenv("RENDER_EXTERNAL_URL", "")
INTERVALO_MANTER_ACORDADO = 600  # 10 minutos (o Render gratuito dorme após 15 sem visitas)

# Impede que a varredura automática e a /forcar-atualizacao rodem ao mesmo tempo
trava_varredura = threading.Lock()


# ==========================================
# 1. ROTAS DO SITE E DA API DE PRODUTOS
# ==========================================

@app.route("/")
def home():
    return render_template("index.html", link_whatsapp=LINK_GRUPO_WHATSAPP)


@app.route("/api/ofertas", methods=["GET"])
def api_ofertas():
    # Puxa as ofertas salvas automaticamente do banco de dados
    ofertas = buscar_ofertas_db()
    return jsonify(ofertas)


@app.route("/saude")
def saude():
    """Rota leve usada para manter o serviço acordado e para monitoramento."""
    return "ok", 200


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

    alternar_interesse(whatsapp_id, categoria)
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
            oferta.get("imagem", ""),
            oferta.get("vendas", 0)
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
    enviadas = 0

    for oferta in ofertas:
        if enviadas >= MAX_ENVIOS_POR_CICLO:
            break  # As demais ficam para o próximo ciclo, evitando excesso de mensagens

        prod_id = oferta["id"]
        if oferta_ja_enviada(prod_id):
            continue

        usuarios = usuarios_da_categoria(oferta["categoria"])

        preco_formatado = f"{oferta['preco']:.2f}".replace(".", ",")
        mensagem = (
            f"🔥 *OFERTA IMPERDÍVEL!* 🔥\n\n"
            f"📦 *{oferta['titulo']}*\n"
            f"💰 Por apenas: *R$ {preco_formatado}*\n\n"
            f"👉 Acesse a promoção: {oferta['link']}"
        )

        for wa_id in usuarios:
            print(f"Enviando oferta para {wa_id}...")
            disparar_para_whatsapp(wa_id, mensagem)
            time.sleep(2)  # Pausa para evitar spam

        # Regista no histórico que o produto já foi disparado
        registrar_envio(prod_id)
        if usuarios:
            enviadas += 1


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


def manter_acordado():
    """Visita o próprio site a cada 10 minutos para o Render gratuito não colocá-lo para dormir."""
    while True:
        time.sleep(INTERVALO_MANTER_ACORDADO)
        try:
            requests.get(f"{URL_PUBLICA}/saude", timeout=30)
        except Exception as e:
            print(f"Aviso: falha ao manter o serviço acordado: {e}")


def iniciar_tarefas_em_segundo_plano():
    # Inicia assim que o app carrega, funcionando tanto com "python main.py" quanto com gunicorn
    threading.Thread(target=processar_ofertas, daemon=True).start()
    if URL_PUBLICA:
        threading.Thread(target=manter_acordado, daemon=True).start()


iniciar_tarefas_em_segundo_plano()


# ==========================================
# 4. INICIALIZAÇÃO DO SERVIDOR
# ==========================================

if __name__ == "__main__":
    # Pega a porta do ambiente (Render) ou usa a porta 5000 por padrão localmente
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
