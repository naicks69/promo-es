"""Integração com o Mercado Livre: link de afiliado, autorização da conta e busca de promoções."""

import base64
import hashlib
import math
import os
import secrets
import threading
import time
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv

from database import gravar_configuracao, ler_configuracao

load_dotenv()

# Aplicativo criado em developers.mercadolivre.com.br (necessário para buscar produtos)
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")

# Identificação de afiliado, tirada do link do Gerador de links do painel de Afiliados
# (parâmetros matt_word e matt_tool). É isso que garante a comissão.
ML_AFILIADO_USUARIO = os.getenv("ML_AFILIADO_USUARIO", "sana8907971")
ML_AFILIADO_FERRAMENTA = os.getenv("ML_AFILIADO_FERRAMENTA", "95146766")

# Desconto mínimo (%) para um produto entrar no site (0 = aceita todos; os com desconto
# continuam tendo prioridade na pontuação)
ML_DESCONTO_MINIMO = float(os.getenv("ML_DESCONTO_MINIMO", "0"))

# O Mercado Livre fica com produtos de mais qualidade: abaixo deste preço o produto
# fica para a Shopee (que traz os itens mais básicos)
ML_PRECO_MINIMO = float(os.getenv("ML_PRECO_MINIMO", "100"))

# O Mercado Livre não informa a comissão de cada produto. Esta taxa estimada só serve
# para comparar as ofertas dele com as da Shopee na hora de escolher o que vai no WhatsApp.
ML_COMISSAO_ESTIMADA = float(os.getenv("ML_COMISSAO_ESTIMADA", "5")) / 100

URL_AUTORIZACAO = "https://auth.mercadolivre.com.br/authorization"
URL_API = "https://api.mercadolibre.com"


def configurado():
    return bool(ML_CLIENT_ID and ML_CLIENT_SECRET)


def tem_renovacao():
    """True quando o site consegue renovar o acesso sozinho (permissão offline_access)."""
    return bool(ler_configuracao("ml_refresh_token"))


def horas_de_acesso_restantes():
    expira_em = int(ler_configuracao("ml_expira_em", "0") or 0)
    return max(0.0, (expira_em - time.time()) / 3600)


def conectado():
    return tem_renovacao() or (bool(ler_configuracao("ml_access_token")) and horas_de_acesso_restantes() > 0)


def registrar_diagnostico(mensagem):
    """Guarda o resultado da última tentativa, exibido na página /mercadolivre."""
    quando = time.strftime("%d/%m %H:%M", time.gmtime(time.time() - 3 * 3600))
    print(f"Mercado Livre: {mensagem}")
    gravar_configuracao("ml_diagnostico", f"{quando} (horário de Brasília): {mensagem}")


def ultimo_diagnostico():
    return ler_configuracao("ml_diagnostico", "")


# Erros e contagens de cada etapa da varredura em andamento (resumidos no fim por
# resumir_varredura e mostrados no painel /admin, para saber onde os produtos se perdem)
erros_da_varredura = []
etapas = Counter()
exemplos_de_descarte = {}


def contar(etapa, quantidade=1, exemplo=None):
    etapas[etapa] += quantidade
    if exemplo and etapa not in exemplos_de_descarte:
        exemplos_de_descarte[etapa] = str(exemplo)[:160]


def registrar_erro_de_busca(mensagem):
    print(f"Mercado Livre: {mensagem}")
    erros_da_varredura.append(mensagem)


# Descartes esperados: a API não libera esses itens para aplicativos de afiliado
DESCARTES_NORMAIS = ("tipo ", "anúncio recusado", "produto sem vendedor", "produto sem preço", "anúncio inativo")


def resumir_varredura(total_categorias, total_ofertas):
    resumo = (
        f"última varredura (mais vendidos): {total_categorias} categorias do Mercado Livre, "
        f"{total_ofertas} produtos com link de afiliado."
    )
    lidos = sum(total for nome, total in etapas.items() if nome.endswith(" lido") or nome.startswith("produto com preço"))
    ignorados = {nome: total for nome, total in etapas.items() if nome.startswith(DESCARTES_NORMAIS)}
    filtrados = {nome: total for nome, total in etapas.items() if nome.startswith(("abaixo", "fora da faixa"))}
    if lidos:
        resumo += f" Produtos lidos: {lidos}."
    if filtrados:
        resumo += " Filtrados: " + ", ".join(f"{nome}: {total}" for nome, total in filtrados.items()) + "."
    if ignorados:
        resumo += (
            f" Ignorados ({sum(ignorados.values())}, normal: a API não libera esses itens): "
            + ", ".join(f"{nome}: {total}" for nome, total in ignorados.items()) + "."
        )
    outros = [nome for nome in exemplos_de_descarte if not nome.startswith(DESCARTES_NORMAIS)]
    if outros:
        resumo += " Exemplos: " + " | ".join(f"{nome} → {exemplos_de_descarte[nome]}" for nome in outros)
    if erros_da_varredura:
        resumo += f" {len(erros_da_varredura)} erro(s). Primeiro erro: {erros_da_varredura[0]}"
    erros_da_varredura.clear()
    etapas.clear()
    exemplos_de_descarte.clear()
    registrar_diagnostico(resumo)


def link_afiliado_mercadolivre(link):
    """Acrescenta a identificação de afiliado ao link do produto."""
    partes = urlsplit(link)
    parametros = [
        (chave, valor) for chave, valor in parse_qsl(partes.query, keep_blank_values=True)
        if chave not in ("matt_word", "matt_tool", "forceInApp")
    ]
    parametros += [
        ("matt_word", ML_AFILIADO_USUARIO),
        ("matt_tool", ML_AFILIADO_FERRAMENTA),
        ("forceInApp", "true"),
    ]
    return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(parametros), ""))


# ==========================================
# AUTORIZAÇÃO DA CONTA (OAuth com PKCE)
# ==========================================

def gerar_link_autorizacao(endereco_retorno, usar_pkce=True):
    """Monta o link que leva à tela do Mercado Livre onde você autoriza o aplicativo.

    O PKCE (code_challenge) deve acompanhar a opção PKCE do aplicativo: se ela estiver
    desligada no site de desenvolvedores, conecte com usar_pkce=False.
    """
    estado = secrets.token_urlsafe(24)
    parametros = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": endereco_retorno,
        "state": estado,
    }
    verificador = ""
    if usar_pkce:
        verificador = secrets.token_urlsafe(64)
        desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
        parametros["code_challenge"] = desafio
        parametros["code_challenge_method"] = "S256"
    gravar_configuracao("ml_oauth_estado", f"{estado}|{verificador}")
    return f"{URL_AUTORIZACAO}?{urlencode(parametros)}"


def _salvar_tokens(dados):
    gravar_configuracao("ml_access_token", dados["access_token"])
    gravar_configuracao("ml_expira_em", str(int(time.time()) + int(dados.get("expires_in", 21600))))
    # O Mercado Livre troca o refresh token a cada renovação; o antigo deixa de valer
    if dados.get("refresh_token"):
        gravar_configuracao("ml_refresh_token", dados["refresh_token"])


def _pedir_token(campos):
    resposta = requests.post(
        f"{URL_API}/oauth/token",
        data={"client_id": ML_CLIENT_ID, "client_secret": ML_CLIENT_SECRET, **campos},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    dados = resposta.json()
    if resposta.status_code != 200 or "access_token" not in dados:
        raise RuntimeError(f"{resposta.status_code}: {dados.get('message') or dados.get('error') or dados}")
    return dados


def concluir_autorizacao(codigo, estado, endereco_retorno):
    """Troca o código recebido do Mercado Livre pelo acesso permanente à conta."""
    guardado = ler_configuracao("ml_oauth_estado", "")
    estado_esperado, _, verificador = guardado.partition("|")
    if not estado or estado != estado_esperado:
        raise RuntimeError("Pedido de autorização expirado ou inválido. Clique em Conectar de novo.")

    campos = {"grant_type": "authorization_code", "code": codigo, "redirect_uri": endereco_retorno}
    if verificador:
        campos["code_verifier"] = verificador
    dados = _pedir_token(campos)
    _salvar_tokens(dados)
    gravar_configuracao("ml_oauth_estado", "")
    if dados.get("refresh_token"):
        registrar_diagnostico("conta conectada, com renovação automática.")
    else:
        registrar_diagnostico(
            "conta conectada SEM renovação automática: o aplicativo está sem a permissão offline_access. "
            "O acesso vale só 6 horas."
        )


# Só uma renovação por vez: o refresh token vale uma única vez, e duas renovações
# simultâneas (varredura + busca de um cliente) faziam a segunda apagar a chave nova
_trava_renovacao = threading.Lock()


def _token_valido():
    token = ler_configuracao("ml_access_token")
    expira_em = int(ler_configuracao("ml_expira_em", "0") or 0)
    return token if token and expira_em - time.time() > 300 else None


def token_de_acesso():
    """Devolve um token válido, renovando sozinho quando está para vencer (dura 6 horas)."""
    token = _token_valido()
    if token:
        return token

    with _trava_renovacao:
        # Outra chamada pode ter renovado enquanto esta esperava
        token = _token_valido()
        if token:
            return token

        refresh = ler_configuracao("ml_refresh_token")
        if not refresh:
            # Sem renovação da conta: usa o acesso do próprio aplicativo
            return token_do_aplicativo()
        try:
            dados = _pedir_token({"grant_type": "refresh_token", "refresh_token": refresh})
        except Exception as e:
            # Só desconecta se a chave recusada ainda for a guardada (não apaga uma chave nova)
            if "invalid_grant" in str(e) and ler_configuracao("ml_refresh_token") == refresh:
                gravar_configuracao("ml_refresh_token", "")
                registrar_diagnostico(
                    f"o Mercado Livre recusou a renovação da conta ({e}). Reconecte em /mercadolivre; "
                    "enquanto isso o site usa o acesso do aplicativo."
                )
            return _token_valido() or token_do_aplicativo()
        _salvar_tokens(dados)
        return dados["access_token"]


def token_do_aplicativo():
    """Acesso em nome do próprio aplicativo (client credentials), sem depender da conta.

    Serve de alternativa quando o Mercado Livre não envia o refresh token da conta.
    Também dura 6 horas, mas o site pede outro sozinho quando vence.
    """
    token = ler_configuracao("ml_app_token")
    expira_em = int(ler_configuracao("ml_app_expira_em", "0") or 0)
    if token and expira_em - time.time() > 300:
        return token
    try:
        dados = _pedir_token({"grant_type": "client_credentials"})
    except Exception as e:
        registrar_diagnostico(
            f"o Mercado Livre recusou o acesso do aplicativo ({e}). "
            "Ative o fluxo Refresh Token (ou Client Credentials) no aplicativo e reconecte a conta."
        )
        return None
    gravar_configuracao("ml_app_token", dados["access_token"])
    gravar_configuracao("ml_app_expira_em", str(int(time.time()) + int(dados.get("expires_in", 21600))))
    return dados["access_token"]


def acesso_do_aplicativo_ativo():
    expira_em = int(ler_configuracao("ml_app_expira_em", "0") or 0)
    return bool(ler_configuracao("ml_app_token")) and expira_em > time.time()


# ==========================================
# BUSCA DE PRODUTOS
# ==========================================
# O Mercado Livre bloqueia a busca aberta (/sites/MLB/search) para aplicativos comuns
# (erro 403). Por isso os produtos são encontrados pelos "Mais vendidos" de cada
# categoria (/highlights) e os detalhes são lidos pelo código do produto, que é liberado.

# Grandes categorias do Mercado Livre usadas para puxar mais produtos em cada área do site
CATEGORIAS_ML_PRINCIPAIS = {
    "casa": ["MLB1574"],                   # Casa, Móveis e Decoração
    "eletronicos": ["MLB1000", "MLB1051"],  # Eletrônicos, Áudio e Vídeo / Celulares e Telefones
    "gamer": ["MLB1144"],                  # Games
    "moda": ["MLB1430"],                   # Calçados, Roupas e Bolsas
}

CACHE_MAIS_VENDIDOS_SEGUNDOS = 3600  # A lista de mais vendidos muda pouco ao longo do dia
CACHE_DETALHES_SEGUNDOS = 1800       # Preço e foto de cada produto são relidos a cada 30 min
_cache_mais_vendidos = {}
_cache_detalhes = {}
_busca_aberta_bloqueada_ate = 0


class ErroApi(Exception):
    def __init__(self, codigo, texto):
        super().__init__(f"código {codigo}: {texto}")
        self.codigo = codigo


def _consultar(caminho, params=None):
    token = token_de_acesso()
    if not token:
        raise ErroApi(401, "sem acesso: conecte a conta em /mercadolivre")
    resposta = requests.get(
        f"{URL_API}{caminho}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=20
    )
    if resposta.status_code != 200:
        raise ErroApi(resposta.status_code, resposta.text[:200])
    return resposta.json()


def imagem_grande(url):
    """Miniaturas terminam em -I.jpg; a versão -O.jpg tem boa resolução."""
    if not url:
        return ""
    url = url.replace("http://", "https://", 1)
    return url.replace("-I.jpg", "-O.jpg").replace("-I.webp", "-O.webp")


def categoria_da_palavra(palavra_chave):
    """Descobre a categoria do Mercado Livre de uma palavra-chave (guardada no banco)."""
    chave = f"ml_categoria:{palavra_chave.lower()}"
    guardada = ler_configuracao(chave)
    if guardada:
        return guardada
    try:
        resultado = _consultar("/sites/MLB/domain_discovery/search", {"q": palavra_chave, "limit": 1})
    except ErroApi as e:
        registrar_erro_de_busca(f"não foi possível descobrir a categoria de '{palavra_chave}' ({e})")
        return None
    categoria = resultado[0].get("category_id") if resultado else None
    if categoria:
        gravar_configuracao(chave, categoria)
    return categoria


def mais_vendidos(categoria_id):
    """Lista de mais vendidos da categoria: [(código, tipo, posição)]."""
    guardado = _cache_mais_vendidos.get(categoria_id)
    if guardado and time.time() - guardado[0] < CACHE_MAIS_VENDIDOS_SEGUNDOS:
        return guardado[1]
    dados = _consultar(f"/highlights/MLB/category/{categoria_id}")
    if not dados.get("content"):
        contar("categoria sem mais vendidos", exemplo=f"{categoria_id}: {str(dados)[:120]}")
    lista = [
        (item["id"], item.get("type", "ITEM"), item.get("position") or posicao + 1)
        for posicao, item in enumerate(dados.get("content", []))
        if item.get("id")
    ]
    for _, tipo, _ in lista:
        contar(f"códigos {tipo}")
    _cache_mais_vendidos[categoria_id] = (time.time(), lista)
    return lista


def _detalhes_de_produto(codigo):
    """Produto de catálogo: preço vem do vendedor que está ganhando a compra (buy box)."""
    dados = _consultar(f"/products/{codigo}")
    vencedor = dados.get("buy_box_winner") or {}
    if not vencedor.get("price"):
        # Sem "vencedor" informado: pega o vendedor mais bem colocado da lista do produto
        try:
            vendedores = _consultar(f"/products/{codigo}/items", {"limit": 1}).get("results") or []
        except ErroApi as e:
            contar("produto sem preço", exemplo=f"{codigo}: lista de vendedores recusada ({e})")
            return None
        if not vendedores or not vendedores[0].get("price"):
            contar("produto sem vendedor ativo", exemplo=codigo)
            return None
        vencedor = vendedores[0]
        contar("produto com preço pela lista de vendedores")
    fotos = dados.get("pictures") or []
    contar("produto lido")
    return {
        "codigo": codigo,
        "titulo": dados.get("name"),
        "preco": vencedor.get("price"),
        "preco_original": vencedor.get("original_price"),
        "link": dados.get("permalink") or f"https://www.mercadolivre.com.br/p/{codigo}",
        "imagem": imagem_grande(fotos[0].get("url") if fotos else ""),
        "vendas": int(vencedor.get("sold_quantity") or 0),
    }


def _detalhes_de_anuncios(codigos):
    """Anúncios comuns, lidos de 20 em 20 numa só consulta."""
    detalhes = []
    for inicio in range(0, len(codigos), 20):
        grupo = codigos[inicio:inicio + 20]
        respostas = _consultar("/items", {
            "ids": ",".join(grupo),
            "attributes": "id,title,price,original_price,permalink,thumbnail,secure_thumbnail,sold_quantity,status",
        })
        for resposta in respostas:
            dados = resposta.get("body") or {}
            if resposta.get("code") != 200:
                contar(f"anúncio recusado ({resposta.get('code')})", exemplo=dados.get("message") or dados)
                continue
            if dados.get("status", "active") != "active" or not dados.get("price"):
                contar("anúncio inativo ou sem preço", exemplo=dados.get("id"))
                continue
            contar("anúncio lido")
            detalhes.append({
                "codigo": dados["id"],
                "titulo": dados.get("title"),
                "preco": dados.get("price"),
                "preco_original": dados.get("original_price"),
                "link": dados.get("permalink"),
                "imagem": imagem_grande(dados.get("secure_thumbnail") or dados.get("thumbnail")),
                "vendas": int(dados.get("sold_quantity") or 0),
            })
    return detalhes


def detalhes_dos_produtos(lista):
    """Recebe [(código, tipo, posição)] e devolve os detalhes, usando o cache de 30 min."""
    agora = time.time()
    resultado, faltando_anuncios = [], []
    for codigo, tipo, posicao in lista:
        guardado = _cache_detalhes.get(codigo)
        if guardado and agora - guardado[0] < CACHE_DETALHES_SEGUNDOS:
            if guardado[1]:
                resultado.append({**guardado[1], "posicao": posicao})
            continue
        if tipo not in ("PRODUCT", "ITEM"):
            contar(f"tipo {tipo} ignorado", exemplo=codigo)
            continue
        if tipo == "PRODUCT":
            try:
                detalhe = _detalhes_de_produto(codigo)
            except ErroApi as e:
                registrar_erro_de_busca(f"não foi possível ler o produto {codigo} ({e})")
                continue
            _cache_detalhes[codigo] = (agora, detalhe)
            if detalhe:
                resultado.append({**detalhe, "posicao": posicao})
        else:
            faltando_anuncios.append((codigo, posicao))

    if faltando_anuncios:
        posicoes = dict(faltando_anuncios)
        try:
            anuncios = _detalhes_de_anuncios([codigo for codigo, _ in faltando_anuncios])
        except ErroApi as e:
            registrar_erro_de_busca(f"não foi possível ler os anúncios ({e})")
            anuncios = []
        for detalhe in anuncios:
            _cache_detalhes[detalhe["codigo"]] = (agora, detalhe)
            resultado.append({**detalhe, "posicao": posicoes.get(detalhe["codigo"])})
    return resultado


def montar_oferta(detalhe, categoria):
    preco = float(detalhe["preco"])
    preco_original = float(detalhe.get("preco_original") or 0)
    tem_desconto = preco_original > preco
    desconto = (preco_original - preco) / preco_original * 100 if tem_desconto else 0
    # Popularidade: vendas informadas ou, na falta delas, a posição nos mais vendidos
    posicao = detalhe.get("posicao")
    popularidade = detalhe.get("vendas") or (1000 / posicao if posicao else 0)
    return {
        "id": f"mercadolivre_{detalhe['codigo']}",
        "titulo": (detalhe.get("titulo") or "").strip(),
        "preco": round(preco, 2),
        "preco_original": round(preco_original, 2) if tem_desconto else None,
        "link": link_afiliado_mercadolivre(detalhe["link"]),
        "categoria": categoria,
        "loja": "mercadolivre",
        "imagem": detalhe.get("imagem") or "",
        "vendas": int(detalhe.get("vendas") or 0),
        "desconto": desconto,
        # Mesma conta da Shopee (comissão estimada × popularidade), com bônus pelo desconto
        "pontuacao": round(preco * ML_COMISSAO_ESTIMADA * math.log10(popularidade + 10) * (1 + desconto / 100), 4),
    }


def buscar_ofertas_da_categoria(categoria_site, palavras_chave, limite, pagina=1):
    """Varredura: mais vendidos das categorias do Mercado Livre ligadas a uma categoria do site.

    A cada varredura lê uma "fatia" diferente da lista de mais vendidos, para trazer
    produtos novos. Produtos com desconto ganham prioridade na pontuação.
    """
    categorias_ml = list(CATEGORIAS_ML_PRINCIPAIS.get(categoria_site, []))
    for palavra in palavras_chave:
        categoria = categoria_da_palavra(palavra)
        if categoria and categoria not in categorias_ml:
            categorias_ml.append(categoria)

    # Olha 3x mais produtos do que precisa: parte dos mais vendidos a API não libera
    # (tipo novo e anúncios bloqueados) e parte fica abaixo do preço mínimo
    por_categoria = max(6, 3 * (limite // max(1, len(categorias_ml)) + 1))
    escolhidos = []
    for categoria_ml in categorias_ml:
        try:
            lista = mais_vendidos(categoria_ml)
        except ErroApi as e:
            registrar_erro_de_busca(f"mais vendidos da categoria {categoria_ml} indisponíveis ({e})")
            continue
        if not lista:
            continue
        inicio = ((pagina - 1) * por_categoria) % len(lista)
        escolhidos.extend((lista + lista)[inicio:inicio + min(por_categoria, len(lista))])

    ofertas = []
    vistos = set()
    for detalhe in detalhes_dos_produtos(escolhidos):
        if detalhe["codigo"] in vistos:
            continue
        if not detalhe.get("titulo") or not detalhe.get("link"):
            contar("sem título ou link", exemplo=detalhe.get("codigo"))
            continue
        vistos.add(detalhe["codigo"])
        oferta = montar_oferta(detalhe, categoria_site)
        if oferta["preco"] < ML_PRECO_MINIMO:
            contar(f"abaixo de R$ {ML_PRECO_MINIMO:g} (fica para a Shopee)")
        elif oferta["desconto"] < ML_DESCONTO_MINIMO:
            contar("abaixo do desconto mínimo")
        else:
            ofertas.append(oferta)

    ofertas.sort(key=lambda o: o["pontuacao"], reverse=True)
    return ofertas[:limite], len(categorias_ml)


def _busca_aberta(termo, limite):
    """Busca normal, que o Mercado Livre costuma bloquear. Fica desligada por 6 h após um 403."""
    global _busca_aberta_bloqueada_ate
    if time.time() < _busca_aberta_bloqueada_ate:
        return None
    try:
        dados = _consultar("/sites/MLB/search", {"q": termo, "limit": limite})
    except ErroApi as e:
        if e.codigo == 403:
            _busca_aberta_bloqueada_ate = time.time() + 6 * 3600
        return None
    return [
        {
            "codigo": p["id"], "titulo": p.get("title"), "preco": p.get("price"),
            "preco_original": p.get("original_price"), "link": p.get("permalink"),
            "imagem": imagem_grande(p.get("thumbnail")), "vendas": int(p.get("sold_quantity") or 0),
        }
        for p in dados.get("results", []) if p.get("id") and p.get("price")
    ]


def buscar_produtos_mercadolivre(palavra_chave, categoria, limite, pagina=1, so_promocoes=False):
    """Busca do cliente: produtos que ele procura, na ordem de relevância, com link de afiliado.

    Tenta a busca normal; se estiver bloqueada, usa a busca do catálogo e, por último,
    os mais vendidos da categoria do termo procurado.
    """
    detalhes = _busca_aberta(palavra_chave, limite)

    if not detalhes:
        try:
            dados = _consultar("/products/search", {"site_id": "MLB", "q": palavra_chave, "status": "active", "limit": limite})
            codigos = [(p["id"], "PRODUCT", None) for p in dados.get("results", []) if p.get("id")]
            detalhes = detalhes_dos_produtos(codigos[:limite])
        except ErroApi as e:
            print(f"Mercado Livre: busca no catálogo indisponível para '{palavra_chave}' ({e})")
            detalhes = []

    if not detalhes:
        categoria_ml = categoria_da_palavra(palavra_chave)
        if categoria_ml:
            try:
                detalhes = detalhes_dos_produtos(mais_vendidos(categoria_ml)[:limite])
            except ErroApi as e:
                print(f"Mercado Livre: mais vendidos indisponíveis para '{palavra_chave}' ({e})")

    ofertas = [
        montar_oferta(d, categoria) for d in detalhes or []
        if d.get("titulo") and d.get("link") and d.get("preco")
    ]
    if so_promocoes:
        ofertas = [o for o in ofertas if o["desconto"] >= ML_DESCONTO_MINIMO]
        ofertas.sort(key=lambda o: o["pontuacao"], reverse=True)
    return ofertas[:limite]
