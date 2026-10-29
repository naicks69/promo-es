import hashlib
import json
import time
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "")
SHOPEE_API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# Quantos produtos trazer por palavra-chave em cada varredura
PRODUTOS_POR_BUSCA = int(os.getenv("PRODUTOS_POR_BUSCA", "6"))

# O que o bot procura na Shopee e em qual categoria do site cada resultado entra.
# Para acrescentar produtos novos, basta incluir uma linha aqui.
BUSCAS_SHOPEE = [
    {"palavra_chave": "garrafa térmica", "categoria": "casa"},
    {"palavra_chave": "organizador cozinha", "categoria": "casa"},
    {"palavra_chave": "fone bluetooth", "categoria": "eletronicos"},
    {"palavra_chave": "smartwatch", "categoria": "eletronicos"},
    {"palavra_chave": "mouse gamer", "categoria": "gamer"},
    {"palavra_chave": "headset gamer", "categoria": "gamer"},
    {"palavra_chave": "camiseta masculina", "categoria": "moda"},
    {"palavra_chave": "óculos de sol", "categoria": "moda"},
]


def credenciais_configuradas():
    return bool(SHOPEE_APP_ID and SHOPEE_SECRET)


def chamar_api_shopee(query):
    """Envia uma consulta GraphQL assinada para a API de Afiliados da Shopee.

    A assinatura exigida pela Shopee é SHA256(AppId + Timestamp + Corpo + Secret),
    onde "Corpo" é exatamente o texto JSON enviado na requisição. Por isso o corpo
    é serializado uma única vez e o mesmo texto é usado na assinatura e no envio.
    """
    timestamp = int(time.time())
    corpo = json.dumps({"query": query}, ensure_ascii=False, separators=(",", ":"))
    assinatura = hashlib.sha256(
        f"{SHOPEE_APP_ID}{timestamp}{corpo}{SHOPEE_SECRET}".encode("utf-8")
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={timestamp}, Signature={assinatura}",
    }

    try:
        resposta = requests.post(SHOPEE_API_URL, data=corpo.encode("utf-8"), headers=headers, timeout=15)
        dados = resposta.json()
    except Exception as e:
        print(f"❌ Erro de conexão com a API da Shopee: {e}")
        return None

    if dados.get("errors"):
        print(f"❌ API da Shopee recusou a consulta: {dados['errors']}")
        return None

    return dados.get("data")


def gerar_link_afiliado_shopee(link_original):
    """Converte um link direto de produto da Shopee em link rastreável de afiliado.

    Retorna None se não conseguir gerar o link, para que nunca seja publicado
    um link sem comissão.
    """
    if not credenciais_configuradas():
        return None

    query = f"""mutation {{
    generateShortLink(input: {{originUrl: {json.dumps(link_original)}}}) {{
        shortLink
    }}
}}"""
    dados = chamar_api_shopee(query)
    if not dados:
        return None
    return (dados.get("generateShortLink") or {}).get("shortLink")


def buscar_produtos_shopee(palavra_chave, categoria, limite=PRODUTOS_POR_BUSCA):
    """Procura produtos reais na Shopee e já devolve o link de afiliado de cada um.

    O campo offerLink da API é o link de afiliado que leva direto à página do
    produto, com a comissão rastreada.
    """
    query = f"""{{
    productOfferV2(keyword: {json.dumps(palavra_chave, ensure_ascii=False)}, sortType: 2, page: 1, limit: {int(limite)}) {{
        nodes {{
            itemId
            shopId
            productName
            priceMin
            priceMax
            imageUrl
            productLink
            offerLink
            sales
            commissionRate
        }}
    }}
}}"""
    dados = chamar_api_shopee(query)
    if not dados:
        return []

    produtos = (dados.get("productOfferV2") or {}).get("nodes") or []
    ofertas = []

    for produto in produtos:
        item_id = produto.get("itemId")
        titulo = (produto.get("productName") or "").strip()
        link_afiliado = produto.get("offerLink")

        # Se a API não trouxe o link de afiliado, gera a partir do link do produto
        if not link_afiliado and produto.get("productLink"):
            link_afiliado = gerar_link_afiliado_shopee(produto["productLink"])

        try:
            preco = float(produto.get("priceMin") or produto.get("priceMax"))
        except (TypeError, ValueError):
            preco = None

        # Só publica produtos completos e com link de afiliado
        if not item_id or not titulo or not link_afiliado or preco is None:
            continue

        ofertas.append({
            "id": f"shopee_{item_id}",
            "titulo": titulo,
            "preco": round(preco, 2),
            "link": link_afiliado,
            "categoria": categoria,
            "loja": "shopee",
            "imagem": produto.get("imageUrl") or "",
        })

    return ofertas


def buscar_ofertas_afiliados():
    """Percorre todas as buscas configuradas e devolve as ofertas com link de afiliado."""
    if not credenciais_configuradas():
        print("⚠️ SHOPEE_APP_ID e SHOPEE_SECRET não configurados. Nenhuma oferta será buscada.")
        return []

    todas_as_ofertas = []
    ids_vistos = set()

    for busca in BUSCAS_SHOPEE:
        ofertas = buscar_produtos_shopee(busca["palavra_chave"], busca["categoria"])
        print(f"🔎 '{busca['palavra_chave']}': {len(ofertas)} produtos com link de afiliado")

        for oferta in ofertas:
            # O mesmo produto pode aparecer em duas buscas diferentes
            if oferta["id"] in ids_vistos:
                continue
            ids_vistos.add(oferta["id"])
            todas_as_ofertas.append(oferta)

        time.sleep(1)  # Pausa entre consultas para não estourar o limite da API

    print(f"Total de produtos com link de afiliado: {len(todas_as_ofertas)}")
    return todas_as_ofertas
