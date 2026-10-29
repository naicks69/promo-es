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
    ultimo_envio,
    registrar_envio,
    salvar_grupo,
    remover_grupo,
    link_convite_grupo,
    agora_utc,
    data_iso,
)
from scraper import buscar_ofertas_afiliados, CATEGORIAS, LOJAS
from whatsapp import interpretar_mensagem, texto_oferta

app = Flask(__name__)
inicializar_banco()

# Número do bot (só dígitos, com DDI e DDD). O site usa para o botão "Falar com o bot".
NUMERO_BOT_WHATSAPP = re.sub(r"\D", "", os.getenv("WHATSAPP_NUMERO_BOT", "5511910032144"))

# Link de convite do grupo de promoções. Normalmente não precisa configurar: o bot
# envia o convite sozinho quando o grupo é ativado com !ativar. Esta variável só
# serve para forçar outro link.
LINK_GRUPO_WHATSAPP = os.getenv("WHATSAPP_LINK", "")

# Senha combinada entre o site e o bot (o bot envia no cabeçalho X-Bot-Token).
# Enquanto não estiver configurada, as rotas do bot ficam bloqueadas.
TOKEN_BOT = os.getenv("WHATSAPP_BOT_TOKEN", "")

# Busca ofertas novas a cada 5 minutos e mantém cada uma no site por até 4 horas
INTERVALO_VARREDURA = int(os.getenv("INTERVALO_VARREDURA", "300"))
HORAS_VALIDADE_OFERTA = float(os.getenv("HORAS_VALIDADE_OFERTA", "4"))

# WhatsApp: a cada 10 minutos, até 3 ofertas novas para cada grupo e pessoa inscrita
INTERVALO_WHATSAPP = int(os.getenv("INTERVALO_WHATSAPP", "600"))
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

def link_do_grupo():
    try:
        return LINK_GRUPO_WHATSAPP or link_convite_grupo()
    except Exception as e:
        print(f"Erro ao buscar o link do grupo: {e}")
        return ""


@app.route("/")
def home():
    link_bot = ""
    if NUMERO_BOT_WHATSAPP:
        link_bot = f"https://wa.me/{NUMERO_BOT_WHATSAPP}"
    return render_template(
        "index.html",
        link_bot=link_bot,
        link_grupo=link_do_grupo(),
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
    return bool(TOKEN_BOT) and request.headers.get("X-Bot-Token") == TOKEN_BOT


def recusar_pedido_do_bot():
    if not TOKEN_BOT:
        return jsonify({"status": "erro", "mensagem": "Configure WHATSAPP_BOT_TOKEN no servidor"}), 503
    return jsonify({"status": "erro", "mensagem": "Token do bot inválido"}), 401


@app.route("/api/whatsapp/mensagem", methods=["POST"])
def mensagem_recebida_no_bot():
    """O bot repassa aqui cada mensagem recebida e envia de volta o campo "resposta".

    Corpo esperado: {"numero": "<id do WhatsApp>", "texto": "<mensagem da pessoa>"}
    """
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()

    dados = request.get_json(silent=True) or {}
    numero = dados.get("numero")
    texto = dados.get("texto") or ""
    if not numero:
        return jsonify({"status": "erro", "mensagem": "Informe numero e texto"}), 400

    acao, categorias, resposta = interpretar_mensagem(texto, INTERVALO_WHATSAPP // 60, link_do_grupo())
    if acao == "inscrever":
        definir_interesses(numero, categorias)
    elif acao == "sair":
        cancelar_inscricao(numero)

    return jsonify({"status": "sucesso", "acao": acao, "categorias": categorias, "resposta": resposta})


@app.route("/api/whatsapp/pendentes", methods=["GET"])
def ofertas_pendentes():
    """O bot consulta esta rota a cada minuto para saber o que enviar.

    Cada grupo ou pessoa inscrita recebe até OFERTAS_POR_MENSAGEM ofertas novas,
    no máximo uma vez a cada INTERVALO_WHATSAPP, e nada no horário de silêncio.
    """
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()
    if em_horario_silencioso():
        return jsonify({"envios": [], "silencio": True})

    limite_ultimo_envio = agora_utc(INTERVALO_WHATSAPP / 3600)
    envios = []
    for destino, categorias in listar_inscritos().items():
        ultimo = ultimo_envio(destino)
        # Compara as datas no mesmo formato ISO (UTC); ainda dentro do intervalo, espera
        if ultimo and ultimo > data_iso(limite_ultimo_envio):
            continue
        ofertas = ofertas_para_enviar(destino, categorias, OFERTAS_POR_MENSAGEM, HORAS_VALIDADE_OFERTA)
        if ofertas:
            envios.append({
                "destino": destino,
                "ofertas": [
                    {"id": o["id"], "imagem": o["imagem"], "texto": texto_oferta(o)} for o in ofertas
                ],
            })
    return jsonify({"envios": envios, "silencio": False})


@app.route("/api/whatsapp/confirmar", methods=["POST"])
def confirmar_envio():
    """O bot avisa quais ofertas conseguiu enviar, para não repeti-las."""
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()
    dados = request.get_json(silent=True) or {}
    destino = dados.get("destino")
    ofertas = dados.get("ofertas") or []
    if not destino or not isinstance(ofertas, list):
        return jsonify({"status": "erro", "mensagem": "Informe destino e ofertas"}), 400
    for produto_id in ofertas:
        registrar_envio(destino, str(produto_id))
    return jsonify({"status": "sucesso", "registradas": len(ofertas)})


@app.route("/api/whatsapp/grupos", methods=["POST"])
def ativar_grupo():
    """O bot registra um grupo quando o dono do número escreve !ativar nele."""
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()
    dados = request.get_json(silent=True) or {}
    jid = dados.get("jid") or ""
    if not jid.endswith("@g.us"):
        return jsonify({"status": "erro", "mensagem": "Informe o jid do grupo"}), 400
    salvar_grupo(jid, dados.get("nome") or "", dados.get("convite") or "")
    return jsonify({"status": "sucesso"})


@app.route("/api/whatsapp/grupos/remover", methods=["POST"])
def desativar_grupo():
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()
    jid = (request.get_json(silent=True) or {}).get("jid")
    if not jid:
        return jsonify({"status": "erro", "mensagem": "Informe o jid do grupo"}), 400
    remover_grupo(jid)
    return jsonify({"status": "sucesso"})


@app.route("/api/interesse", methods=["POST"])
def salvar_interesse():
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()

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


def em_horario_silencioso():
    hora = datetime.now(FUSO_BRASILIA).hour
    if SILENCIO_INICIO > SILENCIO_FIM:
        return hora >= SILENCIO_INICIO or hora < SILENCIO_FIM
    return SILENCIO_INICIO <= hora < SILENCIO_FIM


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
