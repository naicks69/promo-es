"""Integração com o Mercado Livre: link de afiliado, autorização da conta e busca de promoções."""

import base64
import hashlib
import math
import os
import secrets
import time
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

# Só entram no site produtos com pelo menos este desconto (%) sobre o preço anterior
ML_DESCONTO_MINIMO = float(os.getenv("ML_DESCONTO_MINIMO", "10"))

# O Mercado Livre não informa a comissão de cada produto. Esta taxa estimada só serve
# para comparar as ofertas dele com as da Shopee na hora de escolher o que vai no WhatsApp.
ML_COMISSAO_ESTIMADA = float(os.getenv("ML_COMISSAO_ESTIMADA", "5")) / 100

URL_AUTORIZACAO = "https://auth.mercadolivre.com.br/authorization"
URL_API = "https://api.mercadolibre.com"


def configurado():
    return bool(ML_CLIENT_ID and ML_CLIENT_SECRET)


def conectado():
    return bool(ler_configuracao("ml_refresh_token"))


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

def gerar_link_autorizacao(endereco_retorno):
    """Monta o link que leva à tela do Mercado Livre onde você autoriza o aplicativo."""
    estado = secrets.token_urlsafe(24)
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
    gravar_configuracao("ml_oauth_estado", f"{estado}|{verificador}")
    parametros = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": endereco_retorno,
        "state": estado,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
    }
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

    dados = _pedir_token({
        "grant_type": "authorization_code",
        "code": codigo,
        "redirect_uri": endereco_retorno,
        "code_verifier": verificador,
    })
    _salvar_tokens(dados)
    gravar_configuracao("ml_oauth_estado", "")
    if not dados.get("refresh_token"):
        print("⚠️ O Mercado Livre não enviou refresh token: ative a permissão offline_access no aplicativo.")


def token_de_acesso():
    """Devolve um token válido, renovando sozinho quando está para vencer (dura 6 horas)."""
    token = ler_configuracao("ml_access_token")
    expira_em = int(ler_configuracao("ml_expira_em", "0") or 0)
    if token and expira_em - time.time() > 300:
        return token

    refresh = ler_configuracao("ml_refresh_token")
    if not refresh:
        return None
    try:
        dados = _pedir_token({"grant_type": "refresh_token", "refresh_token": refresh})
    except Exception as e:
        print(f"❌ Não foi possível renovar o acesso ao Mercado Livre ({e}). Conecte a conta de novo em /mercadolivre.")
        if "invalid_grant" in str(e):
            gravar_configuracao("ml_refresh_token", "")
        return None
    _salvar_tokens(dados)
    return dados["access_token"]


# ==========================================
# BUSCA DE PROMOÇÕES
# ==========================================

def imagem_grande(url):
    """A busca devolve miniaturas (-I.jpg); a versão -O.jpg tem boa resolução."""
    if not url:
        return ""
    url = url.replace("http://", "https://", 1)
    return url.replace("-I.jpg", "-O.jpg").replace("-I.webp", "-O.webp")


def buscar_produtos_mercadolivre(palavra_chave, categoria, limite, pagina=1):
    """Procura produtos com desconto no Mercado Livre e devolve com link de afiliado."""
    token = token_de_acesso()
    if not token:
        return []

    por_pagina = 50
    try:
        resposta = requests.get(
            f"{URL_API}/sites/MLB/search",
            params={"q": palavra_chave, "limit": por_pagina, "offset": (pagina - 1) * por_pagina},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except Exception as e:
        print(f"❌ Erro de conexão com o Mercado Livre: {e}")
        return []

    if resposta.status_code != 200:
        print(f"❌ Mercado Livre recusou a busca '{palavra_chave}' ({resposta.status_code}): {resposta.text[:300]}")
        return []

    ofertas = []
    for produto in resposta.json().get("results", []):
        preco = produto.get("price")
        preco_original = produto.get("original_price")
        link = produto.get("permalink")
        titulo = (produto.get("title") or "").strip()
        if not (produto.get("id") and titulo and link and preco):
            continue

        # Promoção = preço atual menor que o anterior, com desconto mínimo
        if not preco_original or preco_original <= preco:
            continue
        desconto = (preco_original - preco) / preco_original * 100
        if desconto < ML_DESCONTO_MINIMO:
            continue

        vendas = int(produto.get("sold_quantity") or 0)
        ofertas.append({
            "id": f"mercadolivre_{produto['id']}",
            "titulo": titulo,
            "preco": round(float(preco), 2),
            "preco_original": round(float(preco_original), 2),
            "link": link_afiliado_mercadolivre(link),
            "categoria": categoria,
            "loja": "mercadolivre",
            "imagem": imagem_grande(produto.get("thumbnail")),
            "vendas": vendas,
            # Mesma conta usada na Shopee (comissão estimada × popularidade), com um
            # bônus pelo tamanho do desconto, que ajuda a converter a venda
            "pontuacao": round(
                float(preco) * ML_COMISSAO_ESTIMADA * math.log10(vendas + 10) * (1 + desconto / 100), 4
            ),
        })

    ofertas.sort(key=lambda o: o["pontuacao"], reverse=True)
    return ofertas[:limite]
