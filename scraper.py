import hashlib
import time
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "")
AMAZON_TAG = os.getenv("AMAZON_TAG", "")
MERCADO_LIVRE_AFFILIATE_ID = os.getenv("ML_AFFILIATE_ID", "")


def gerar_link_afiliado_shopee(link_original):
    """Converte automaticamente qualquer link da Shopee em link rastreável."""
    if not SHOPEE_APP_ID or not SHOPEE_SECRET:
        return link_original
        
    url_api = "https://open-api.affiliate.shopee.com.br/graphql"
    timestamp = int(time.time())

    query = f"""
    mutation {{
        generateShortLink(input: {{originUrl: "{link_original}"}}) {{
            shortLink
        }}
    }}
    """
    payload = {"query": query}
    signature_base = f"{SHOPEE_APP_ID}{timestamp}{query}{SHOPEE_SECRET}"
    signature = hashlib.sha256(signature_base.encode("utf-8")).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={timestamp}, Signature={signature}",
    }

    try:
        resposta = requests.post(url_api, json=payload, headers=headers, timeout=10)
        dados = resposta.json()
        return dados["data"]["generateShortLink"]["shortLink"]
    except Exception as e:
        print(f"Erro ao gerar link Shopee: {e}")
        return link_original


def buscar_ofertas_mercadolivre_auto(termo="ofertas"):
    """Busca produtos automaticamente na API pública do Mercado Livre."""
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={termo}&limit=5"
    ofertas = []
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        for item in data.get("results", []):
            link_original = item.get("permalink")
            # Aplica o ID de afiliado do Mercado Livre automaticamente
            link_afiliado = f"{link_original}?matt_tool={MERCADO_LIVRE_AFFILIATE_ID}" if MERCADO_LIVRE_AFFILIATE_ID else link_original
            
            ofertas.append({
                "id": f"ML_{item.get('id')}",
                "titulo": item.get("title"),
                "preco": item.get("price"),
                "link": link_afiliado,
                "categoria": "geral",
                "loja": "mercadolivre",
                "imagem": item.get("thumbnail").replace("http:", "https:")
            })
    except Exception as e:
        print(f"Erro ao buscar Mercado Livre: {e}")
    return ofertas


def buscar_ofertas_afiliados():
    """Função central que roda periodicamente para puxar novidades de forma autônoma."""
    todas_as_ofertas = []

    # 1. Puxa automaticamente do Mercado Livre com base em termos de busca populares
    termos_busca = ["smartphone", "fone bluetooth", "smartwatch", "eletroportateis"]
    for termo in termos_busca:
        produtos_ml = buscar_ofertas_mercadolivre_auto(termo)
        todas_as_ofertas.extend(produtos_ml)

    # 2. Aqui você pode adicionar lógica semelhante para Shopee/Amazon via API de busca ou listagens automatizadas.
    
    print(f"Total de ofertas puxadas automaticamente: {len(todas_as_ofertas)}")
    return todas_as_ofertas