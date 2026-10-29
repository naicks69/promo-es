import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, jsonify, render_template, request
from database import (
    inicializar_banco,
    salvar_oferta_db,
    buscar_ofertas_db,
    remover_ofertas_expiradas,
    alternar_interesse,
    definir_interesses,
    cancelar_inscricao,
    listar_inscritos,
    ofertas_para_enviar,
    registrar_envio,
    agora_utc,
    data_iso,
)
from scraper import buscar_ofertas_afiliados, CATEGORIAS, LOJAS
from whatsapp import interpretar_mensagem, montar_mensagem_ofertas

app = Flask(__name__)
inicializar_banco()

# Endereço do bot de WhatsApp. No Render, configure a variável WHATSAPP_BOT_URL
# com o endereço público do bot (127.0.0.1 só funciona se o bot rodar no mesmo servidor).
NODE_SERVER_URL = os.getenv("WHATSAPP_BOT_URL", "http://127.0.0.1:3000/send-promo")

# Número do bot (só dígitos, com DDI e DDD, ex.: 5511999998888). O botão do site abre
# uma conversa com ele já com a mensagem de inscrição escrita.
NUMERO_BOT_WHATSAPP = re.sub(r"\D", "", os.getenv("WHATSAPP_NUMERO_BOT", ""))

# Link de convite do grupo/canal de WhatsApp (opcional, aparece como alternativa)
LINK_GRUPO_WHATSAPP = os.getenv("WHATSAPP_LINK", "")

# Chave que o bot envia no cabeçalho X-Bot-Token para cadastrar pessoas.
# Sem ela configurada, qualquer um poderia inscrever números de terceiros.
TOKEN_BOT = os.getenv("WHATSAPP_BOT_TOKEN", "")

# Busca ofertas novas a cada 5 minutos e mantém cada uma no site por até 4 horas
INTERVALO_VARREDURA = int(os.getenv("INTERVALO_VARREDURA", "300"))
HORAS_VALIDADE_OFERTA = float(os.getenv("HORAS_VALIDADE_OFERTA", "4"))

# Envio no WhatsApp: a cada 30 minutos, até 3 ofertas novas por pessoa numa só mensagem
INTERVALO_WHATSAPP = int(os.getenv("INTERVALO_WHATSAPP", "1800"))
OFERTAS_POR_MENSAGEM = int(os.getenv("OFERTAS_POR_MENSAGEM", "3"))

# Horário de Brasília em que o bot não manda ofertas (das 22h às 8h)
FUSO_BRASILIA = timezone(timedelta(hours=-3))
SILENCIO_INICIO = int(os.getenv("SILENCIO_INICIO", "22"))
SILENCIO_FIM = int(os.getenv("SILENCIO_FIM", "8"))

# Endereço público do próprio site. O Render preenche RENDER_EXTERNAL_URL sozinho.
URL_PUBLICA = os.getenv("RENDER_EXTERNAL_URL", "")
INTERVALO_MANTER_ACORDADO = 600  # 10 minutos (o Render gratuito dorme após 15 sem visitas)

# Impede que a varredura automática e a /forcar-atualizacao rodem ao mesmo tempo
trava_varredura = threading.Lock()
ultima_varredura = None


# ==========================================
# 1. ROTAS DO SITE E DA API DE PRODUTOS
# ==========================================

@app.route("/")
def home():
    link_bot = ""
    if NUMERO_BOT_WHATSAPP:
        link_bot = f"https://wa.me/{NUMERO_BOT_WHATSAPP}"
    return render_template(
        "index.html",
        link_bot=link_bot,
        link_grupo=LINK_GRUPO_WHATSAPP,
        categorias=CATEGORIAS,
        lojas=LOJAS,
        intervalo_whatsapp=INTERVALO_WHATSAPP // 60,
        horas_validade=f"{HORAS_VALIDADE_OFERTA:g}",
    )


@app.route("/api/ofertas", methods=["GET"])
def api_ofertas():
    # Só as ofertas publicadas dentro do prazo de validade
    ofertas = buscar_ofertas_db(HORAS_VALIDADE_OFERTA)
    return jsonify(ofertas)


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "ultima_varredura": data_iso(ultima_varredura),
        "intervalo_minutos": INTERVALO_VARREDURA // 60,
        "validade_horas": HORAS_VALIDADE_OFERTA,
    })


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
        total = executar_varredura()
        return f"🔄 Busca forçada executada com sucesso! O bot encontrou {total} produtos e salvou-os no banco de dados."
    except Exception as e:
        return f"❌ Erro ao forçar atualização: {e}", 500
    finally:
        trava_varredura.release()


# ==========================================
# 2. ROTAS DO BOT DE WHATSAPP
# ==========================================

def pedido_do_bot_autorizado():
    return not TOKEN_BOT or request.headers.get("X-Bot-Token") == TOKEN_BOT


@app.route("/api/whatsapp/mensagem", methods=["POST"])
def mensagem_recebida_no_bot():
    """O bot repassa aqui cada mensagem recebida e envia de volta o campo "resposta".

    Corpo esperado: {"numero": "<id do WhatsApp>", "texto": "<mensagem da pessoa>"}
    """
    if not pedido_do_bot_autorizado():
        return jsonify({"status": "erro", "mensagem": "Token do bot inválido"}), 401

    dados = request.get_json(silent=True) or {}
    numero = dados.get("numero")
    texto = dados.get("texto") or ""
    if not numero:
        return jsonify({"status": "erro", "mensagem": "Informe numero e texto"}), 400

    acao, categorias, resposta = interpretar_mensagem(texto, INTERVALO_WHATSAPP // 60)
    if acao == "inscrever":
        definir_interesses(numero, categorias)
    elif acao == "sair":
        cancelar_inscricao(numero)

    return jsonify({"status": "sucesso", "acao": acao, "categorias": categorias, "resposta": resposta})


@app.route("/api/interesse", methods=["POST"])
def salvar_interesse():
    if not pedido_do_bot_autorizado():
        return jsonify({"status": "erro", "mensagem": "Token do bot inválido"}), 401

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


def executar_varredura():
    """Busca ofertas novas, salva no banco e tira do site as que passaram do prazo."""
    global ultima_varredura
    total = salvar_ofertas(buscar_ofertas_afiliados())
    remover_ofertas_expiradas(HORAS_VALIDADE_OFERTA)
    ultima_varredura = agora_utc()
    return total


def processar_ofertas():
    while True:
        print("\n🔍 Buscando novas ofertas no mercado...")
        with trava_varredura:
            try:
                executar_varredura()
                print("✅ Ciclo de varredura concluído com sucesso!")
            except Exception as e:
                print(f"Erro no processamento de ofertas: {e}")

        time.sleep(INTERVALO_VARREDURA)


def disparar_para_whatsapp(whatsapp_id, texto):
    try:
        resposta = requests.post(
            NODE_SERVER_URL,
            json={"numero": whatsapp_id, "mensagem": texto},
            timeout=10,
        )
        return resposta.ok
    except Exception as e:
        print(f"Erro ao disparar mensagem para {whatsapp_id}: {e}")
        return False


def em_horario_silencioso():
    hora = datetime.now(FUSO_BRASILIA).hour
    if SILENCIO_INICIO > SILENCIO_FIM:
        return hora >= SILENCIO_INICIO or hora < SILENCIO_FIM
    return SILENCIO_INICIO <= hora < SILENCIO_FIM


def enviar_ofertas_whatsapp():
    """Manda para cada inscrito uma mensagem com as ofertas novas das categorias dele."""
    for whatsapp_id, categorias in listar_inscritos().items():
        ofertas = ofertas_para_enviar(
            whatsapp_id, categorias, OFERTAS_POR_MENSAGEM, HORAS_VALIDADE_OFERTA
        )
        if not ofertas:
            continue

        print(f"Enviando {len(ofertas)} oferta(s) para {whatsapp_id}...")
        if disparar_para_whatsapp(whatsapp_id, montar_mensagem_ofertas(ofertas)):
            # Só marca como enviada se o bot confirmou; senão tenta no próximo ciclo
            for oferta in ofertas:
                registrar_envio(whatsapp_id, oferta["id"])
        time.sleep(3)  # Pausa entre pessoas para o WhatsApp não bloquear o número


def processar_whatsapp():
    # Espera a primeira varredura terminar antes do primeiro envio
    time.sleep(60)
    while True:
        if em_horario_silencioso():
            print("🌙 Horário de silêncio: nenhuma oferta enviada no WhatsApp agora.")
        else:
            try:
                enviar_ofertas_whatsapp()
            except Exception as e:
                print(f"Erro no envio de ofertas pelo WhatsApp: {e}")
        time.sleep(INTERVALO_WHATSAPP)


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
    threading.Thread(target=processar_whatsapp, daemon=True).start()
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
