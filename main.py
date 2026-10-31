import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
import requests
import hashlib
import hmac
from flask import Flask, jsonify, redirect, render_template, request, session
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
    contar_ofertas_por_loja,
    ofertas_ativas,
    publicados_desde,
    registrar_publicacao,
    limpar_publicacoes_antigas,
    contar_grupos,
    agora_utc,
    data_iso,
    salvar_feedback,
    feedbacks_pendentes,
    marcar_feedbacks_enviados,
    ultimos_feedbacks,
    limpar_feedbacks_antigos,
)
from collections import deque
from scraper import buscar_ofertas_afiliados, filtrar_repetidos, pesquisar_nas_lojas, link_busca_amazon, CATEGORIAS, LOJAS
import aliexpress
import mercadolivre
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

# Chave que assina o cookie de login da página /mercadolivre (derivada da senha do bot)
app.secret_key = hashlib.sha256(f"radar-de-ofertas-sessao:{TOKEN_BOT}".encode()).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("RENDER_EXTERNAL_URL", "").startswith("https://"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Busca ofertas novas a cada 5 minutos e mantém cada uma no site por até 4 horas
INTERVALO_VARREDURA = int(os.getenv("INTERVALO_VARREDURA", "300"))
HORAS_VALIDADE_OFERTA = float(os.getenv("HORAS_VALIDADE_OFERTA", "4"))

# Um produto que saiu do site só pode voltar depois deste tempo (evita repetição)
HORAS_SEM_REPETIR = float(os.getenv("HORAS_SEM_REPETIR", "24"))

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


# Busca do cliente nas lojas: guarda cada resultado por 15 minutos e limita
# a 8 buscas por minuto por pessoa, para não estourar o limite das APIs
CACHE_BUSCA_SEGUNDOS = 900
BUSCAS_POR_MINUTO = 8
cache_buscas = {}
buscas_recentes = {}
trava_busca = threading.Lock()


def ip_do_visitante():
    # No Render o pedido passa por um proxy; o IP real vem no X-Forwarded-For
    encaminhado = request.headers.get("X-Forwarded-For", "")
    return encaminhado.split(",")[0].strip() or request.remote_addr or ""


@app.route("/api/buscar", methods=["GET"])
def buscar_nas_lojas():
    termo = " ".join((request.args.get("q") or "").split())[:80]
    if len(termo) < 2:
        return jsonify({"status": "erro", "mensagem": "Digite pelo menos 2 letras"}), 400

    agora = time.time()
    chave = termo.lower()
    with trava_busca:
        guardado = cache_buscas.get(chave)
        if guardado and agora - guardado[0] < CACHE_BUSCA_SEGUNDOS:
            return jsonify(guardado[1])

        historico = buscas_recentes.setdefault(ip_do_visitante(), deque())
        while historico and agora - historico[0] > 60:
            historico.popleft()
        if len(historico) >= BUSCAS_POR_MINUTO:
            return jsonify({"status": "erro", "mensagem": "Muitas buscas seguidas. Espere um minuto."}), 429
        historico.append(agora)

    try:
        resultados = pesquisar_nas_lojas(termo)
    except Exception as e:
        print(f"Erro na busca do cliente '{termo}': {e}")
        resultados = []

    resposta = {"termo": termo, "resultados": resultados, "amazon": link_busca_amazon(termo)}
    with trava_busca:
        if len(cache_buscas) > 300:
            cache_buscas.clear()
        if resultados:
            cache_buscas[chave] = (agora, resposta)
    return jsonify(resposta)


# Feedback do rodapé: até 3 mensagens a cada 10 minutos por pessoa
FEEDBACKS_POR_JANELA = 3
JANELA_FEEDBACK_SEGUNDOS = 600
feedbacks_recentes = {}


@app.route("/api/feedback", methods=["POST"])
def receber_feedback():
    """Guarda o comentário do cliente; o bot repassa para o WhatsApp do próprio número."""
    dados = request.get_json(silent=True) or {}
    # Campo escondido: só robôs de spam preenchem
    if dados.get("site"):
        return jsonify({"status": "sucesso"})
    texto = str(dados.get("texto") or "").strip()[:1000]
    contato = " ".join(str(dados.get("contato") or "").split())[:80]
    if len(texto) < 3:
        return jsonify({"status": "erro", "mensagem": "Escreva um pouco mais."}), 400

    agora = time.time()
    with trava_busca:
        historico = feedbacks_recentes.setdefault(ip_do_visitante(), deque())
        while historico and agora - historico[0] > JANELA_FEEDBACK_SEGUNDOS:
            historico.popleft()
        if len(historico) >= FEEDBACKS_POR_JANELA:
            return jsonify({"status": "erro", "mensagem": "Recebemos suas mensagens. Tente de novo daqui a alguns minutos."}), 429
        historico.append(agora)
        if len(feedbacks_recentes) > 2000:
            feedbacks_recentes.clear()

    salvar_feedback(texto, contato)
    return jsonify({"status": "sucesso"})


@app.route("/api/whatsapp/feedbacks", methods=["GET"])
def feedbacks_para_o_bot():
    """O bot busca os feedbacks novos e manda no WhatsApp do próprio número."""
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()
    feedbacks = feedbacks_pendentes()
    for f in feedbacks:
        quando = ""
        if f["data"]:
            momento = datetime.fromisoformat(f["data"].replace("Z", "+00:00")) - timedelta(hours=3)
            quando = momento.strftime("%d/%m às %H:%M")
        texto = f"💬 *Feedback do site*\n\n{f['texto']}"
        if f["contato"]:
            texto += f"\n\n📇 Contato: {f['contato']}"
        if quando:
            texto += f"\n\n🕒 {quando} (Brasília)"
        f["mensagem"] = texto
    return jsonify({"feedbacks": feedbacks})


@app.route("/api/whatsapp/feedbacks/confirmar", methods=["POST"])
def confirmar_feedbacks():
    if not pedido_do_bot_autorizado():
        return recusar_pedido_do_bot()
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"status": "erro", "mensagem": "Informe a lista de ids"}), 400
    marcar_feedbacks_enviados(ids)
    limpar_feedbacks_antigos()
    return jsonify({"status": "sucesso", "registrados": len(ids)})


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
# 3. CONEXÃO COM O MERCADO LIVRE
# ==========================================
# Página de administração protegida pela mesma senha do bot (WHATSAPP_BOT_TOKEN).
# A senha é digitada uma vez num formulário e fica guardada num cookie assinado,
# para não aparecer no endereço da página nem no histórico do navegador.

def senha_admin_correta():
    if not TOKEN_BOT:
        return False
    return session.get("admin") == hashlib.sha256(TOKEN_BOT.encode()).hexdigest()


def entrar_como_admin(senha):
    if TOKEN_BOT and hmac.compare_digest(senha or "", TOKEN_BOT):
        session.permanent = True
        session["admin"] = hashlib.sha256(TOKEN_BOT.encode()).hexdigest()
        return True
    return False


def endereco_retorno_mercadolivre():
    base = URL_PUBLICA or request.host_url.rstrip("/")
    return f"{base}/mercadolivre/retorno"


def pagina_mercadolivre(mensagem="", erro=False, status=200):
    exemplo = mercadolivre.link_afiliado_mercadolivre(
        "https://www.mercadolivre.com.br/exemplo-de-produto/p/MLB00000000"
    )
    return render_template(
        "mercadolivre.html",
        configurado=mercadolivre.configurado(),
        conectado=mercadolivre.conectado(),
        tem_renovacao=mercadolivre.tem_renovacao(),
        horas_restantes=mercadolivre.horas_de_acesso_restantes(),
        diagnostico=mercadolivre.ultimo_diagnostico(),
        endereco_retorno=endereco_retorno_mercadolivre(),
        usuario_afiliado=mercadolivre.ML_AFILIADO_USUARIO,
        ferramenta_afiliado=mercadolivre.ML_AFILIADO_FERRAMENTA,
        exemplo_link=exemplo,
        mensagem=mensagem,
        erro=erro,
        senha_configurada=bool(TOKEN_BOT),
        senha_correta=senha_admin_correta(),
    ), status


@app.route("/mercadolivre", methods=["GET", "POST"])
def status_mercadolivre():
    senha = request.form.get("senha") if request.method == "POST" else request.args.get("senha")
    if senha is not None:
        if entrar_como_admin(senha):
            # Recarrega sem a senha no endereço
            return redirect("/mercadolivre")
        return pagina_mercadolivre("Senha incorreta.", erro=True, status=403)
    if not senha_admin_correta():
        return pagina_mercadolivre(status=403)
    return pagina_mercadolivre()


@app.route("/admin", methods=["GET", "POST"])
def painel_admin():
    """Situação de todas as lojas, do banco e do WhatsApp numa tela só."""
    if request.method == "POST":
        if entrar_como_admin(request.form.get("senha")):
            return redirect("/admin")
        return render_template("admin.html", senha_configurada=bool(TOKEN_BOT), senha_correta=False,
                               mensagem="Senha incorreta."), 403
    if not senha_admin_correta():
        return render_template("admin.html", senha_configurada=bool(TOKEN_BOT), senha_correta=False), 403

    from database import USA_POSTGRES
    ofertas = contar_ofertas_por_loja(HORAS_VALIDADE_OFERTA)
    faltando_aliexpress = [
        nome for nome, valor in (
            ("ALIEXPRESS_APP_KEY", aliexpress.ALIEXPRESS_APP_KEY),
            ("ALIEXPRESS_APP_SECRET", aliexpress.ALIEXPRESS_APP_SECRET),
            ("ALIEXPRESS_TRACKING_ID", aliexpress.ALIEXPRESS_TRACKING_ID),
        ) if not valor
    ]
    from scraper import credenciais_configuradas as shopee_configurada
    return render_template(
        "admin.html",
        senha_configurada=True,
        senha_correta=True,
        usa_postgres=USA_POSTGRES,
        ofertas=ofertas,
        lojas=LOJAS,
        ultima_varredura=data_iso(ultima_varredura),
        shopee_configurada=shopee_configurada(),
        ml_configurado=mercadolivre.configurado(),
        ml_conectado=mercadolivre.conectado(),
        ml_renovacao=mercadolivre.tem_renovacao(),
        ml_acesso_aplicativo=mercadolivre.acesso_do_aplicativo_ativo(),
        ml_diagnostico=mercadolivre.ultimo_diagnostico(),
        aliexpress_configurado=aliexpress.configurado(),
        faltando_aliexpress=faltando_aliexpress,
        aliexpress_diagnostico=aliexpress.ultimo_diagnostico(),
        grupos=contar_grupos(),
        link_grupo=link_do_grupo(),
        feedbacks=ultimos_feedbacks(),
    )


@app.route("/mercadolivre/conectar")
def conectar_mercadolivre():
    if not senha_admin_correta():
        return pagina_mercadolivre(status=403)
    if not mercadolivre.configurado():
        return pagina_mercadolivre("Cadastre ML_CLIENT_ID e ML_CLIENT_SECRET no Render antes de conectar.", erro=True)
    usar_pkce = request.args.get("pkce", "1") != "0"
    return redirect(mercadolivre.gerar_link_autorizacao(endereco_retorno_mercadolivre(), usar_pkce))


@app.route("/mercadolivre/retorno")
def retorno_mercadolivre():
    """O Mercado Livre manda a pessoa de volta para cá depois da autorização."""
    if request.args.get("error"):
        return pagina_mercadolivre(
            f"O Mercado Livre não autorizou: {request.args.get('error_description') or request.args.get('error')}",
            erro=True,
        )
    try:
        mercadolivre.concluir_autorizacao(
            request.args.get("code", ""), request.args.get("state", ""), endereco_retorno_mercadolivre()
        )
    except Exception as e:
        return pagina_mercadolivre(f"Não foi possível concluir a conexão: {e}", erro=True)
    if not mercadolivre.tem_renovacao():
        return pagina_mercadolivre(
            "⚠️ Conectado, mas o Mercado Livre não liberou a renovação automática: o acesso vale só 6 horas. "
            "Ative a permissão offline_access no aplicativo e conecte de novo.",
            erro=True,
        )
    return pagina_mercadolivre(
        "✅ Conta do Mercado Livre conectada! As promoções começam a aparecer na próxima busca (até 5 minutos)."
    )


# ==========================================
# 4. FUNÇÕES DE AUTOMAÇÃO E WHATSAPP
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
            oferta.get("vendas", 0),
            oferta.get("preco_original"),
            oferta.get("pontuacao", 0),
        )
    return len(ofertas)


def executar_varredura():
    """Busca ofertas novas, salva no banco e tira do site as que passaram do prazo."""
    global ultima_varredura
    remover_ofertas_expiradas(HORAS_VALIDADE_OFERTA)
    ativas = ofertas_ativas(HORAS_VALIDADE_OFERTA)
    ofertas, repetidas = filtrar_repetidos(
        buscar_ofertas_afiliados(), ativas, publicados_desde(HORAS_SEM_REPETIR)
    )
    ids_ativos = {id_ for id_, _ in ativas}
    for oferta in ofertas:
        if oferta["id"] not in ids_ativos:
            registrar_publicacao(oferta["id"], oferta["titulo"])
    limpar_publicacoes_antigas(max(72, HORAS_SEM_REPETIR * 2))
    total = salvar_ofertas(ofertas)
    print(f"Varredura: {total} ofertas salvas, {repetidas} repetidas ou parecidas deixadas de fora.")
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
# 5. INICIALIZAÇÃO DO SERVIDOR
# ==========================================

if __name__ == "__main__":
    # Pega a porta do ambiente (Render) ou usa a porta 5000 por padrão localmente
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
