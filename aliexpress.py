"""Integração com o AliExpress pela API de afiliados (AliExpress Open Platform / Portals)."""

import hashlib
import hmac
import math
import os
import time

import requests
from dotenv import load_dotenv

from database import gravar_configuracao, ler_configuracao

load_dotenv()

# Criados em portals.aliexpress.com (afiliado) e na Open Platform (aplicativo)
ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY", "")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")
# Tracking ID do painel de afiliado: é ele que identifica a sua comissão nos links
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "")

URL_API = "https://api-sg.aliexpress.com/sync"


def configurado():
    return bool(ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET and ALIEXPRESS_TRACKING_ID)


def registrar_diagnostico(mensagem):
    """Guarda o resultado da última varredura, exibido no painel /admin."""
    quando = time.strftime("%d/%m %H:%M", time.gmtime(time.time() - 3 * 3600))
    print(f"AliExpress: {mensagem}")
    gravar_configuracao("aliexpress_diagnostico", f"{quando} (horário de Brasília): {mensagem}")


def ultimo_diagnostico():
    return ler_configuracao("aliexpress_diagnostico", "")


def assinar(parametros):
    """Assinatura HMAC-SHA256 exigida pela API: chaves em ordem alfabética, chave+valor colados."""
    base = "".join(f"{chave}{parametros[chave]}" for chave in sorted(parametros))
    return hmac.new(ALIEXPRESS_APP_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def chamar_api(metodo, parametros_negocio):
    parametros = {
        "method": metodo,
        "app_key": ALIEXPRESS_APP_KEY,
        "timestamp": str(int(time.time() * 1000)),
        "sign_method": "sha256",
        **{chave: str(valor) for chave, valor in parametros_negocio.items() if valor not in (None, "")},
    }
    parametros["sign"] = assinar(parametros)

    resposta = requests.post(URL_API, data=parametros, timeout=20)
    dados = resposta.json()
    if "error_response" in dados:
        erro = dados["error_response"]
        raise RuntimeError(f"{erro.get('code')}: {erro.get('msg')}")

    chave_resposta = metodo.replace(".", "_") + "_response"
    resultado = (dados.get(chave_resposta) or {}).get("resp_result") or {}
    if str(resultado.get("resp_code")) != "200":
        raise RuntimeError(f"{resultado.get('resp_code')}: {resultado.get('resp_msg')}")
    return (resultado.get("result") or {}).get("products", {}).get("product", []) or []


def numero(valor):
    """Converte "12.34", "7.0%" ou "1,234" em número."""
    try:
        return float(str(valor).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def buscar_produtos_aliexpress(palavra_chave, categoria, limite, pagina=1, so_promocoes=True):
    """Busca produtos com link de afiliado.

    Nas varreduras (so_promocoes=True) usa os produtos "hot" do programa de afiliados,
    que costumam pagar comissão maior, e fica com os que mais devem render. Na busca do
    cliente, usa a busca normal, na ordem de relevância.
    """
    metodo = "aliexpress.affiliate.hotproduct.query" if so_promocoes else "aliexpress.affiliate.product.query"
    try:
        produtos = chamar_api(metodo, {
            "keywords": palavra_chave,
            "page_no": pagina,
            "page_size": 50 if so_promocoes else max(limite, 20),
            "sort": "LAST_VOLUME_DESC" if so_promocoes else "",
            "target_currency": "BRL",
            "target_language": "PT",
            "ship_to_country": "BR",
            "tracking_id": ALIEXPRESS_TRACKING_ID,
        })
    except Exception as e:
        print(f"AliExpress: erro na busca '{palavra_chave}': {e}")
        raise

    ofertas = []
    for produto in produtos:
        titulo = (produto.get("product_title") or "").strip()
        link = produto.get("promotion_link")  # link de afiliado com o seu tracking ID
        preco = numero(produto.get("target_sale_price") or produto.get("sale_price"))
        preco_original = numero(produto.get("target_original_price") or produto.get("original_price"))
        if not (produto.get("product_id") and titulo and link and preco > 0):
            continue

        taxa = numero(produto.get("hot_product_commission_rate") or produto.get("commission_rate")) / 100
        vendas = int(numero(produto.get("lastest_volume")))
        ofertas.append({
            "id": f"aliexpress_{produto['product_id']}",
            "titulo": titulo,
            "preco": round(preco, 2),
            "preco_original": round(preco_original, 2) if preco_original > preco else None,
            "link": link,
            "categoria": categoria,
            "loja": "aliexpress",
            "imagem": produto.get("product_main_image_url") or "",
            "vendas": vendas,
            # Mesma conta da Shopee: comissão em R$ por venda × popularidade
            "pontuacao": round(preco * taxa * math.log10(vendas + 10), 4),
        })

    if so_promocoes:
        ofertas.sort(key=lambda o: o["pontuacao"], reverse=True)
    return ofertas[:limite]
