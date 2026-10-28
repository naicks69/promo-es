import hashlib
import time
import os
import requests
from dotenv import load_dotenv

# Carrega as chaves do arquivo .env se ele existir
load_dotenv()

# --- CONFIGURAÇÕES DAS APIS DE AFILIADOS ---
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "seu_app_id_aqui")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "seu_secret_aqui")
AMAZON_TAG = os.getenv("AMAZON_TAG", "seu_tag-20")
MERCADO_LIVRE_AFFILIATE_ID = os.getenv("ML_AFFILIATE_ID", "seu_id_afiliado_ml")


def gerar_link_afiliado_shopee(link_original):
    """Converte um link comum da Shopee em link de afiliado rastreável via API GraphQL."""
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
        "Authorization": (
            f"SHA256 Credential={SHOPEE_APP_ID},"
            f" Timestamp={timestamp}, Signature={signature}"
        ),
    }

    try:
        resposta = requests.post(url_api, json=payload, headers=headers, timeout=10)
        dados = resposta.json()
        return dados["data"]["generateShortLink"]["shortLink"]
    except Exception as e:
        print(f"Erro ao gerar link Shopee: {e}")
        return link_original


def gerar_link_mercadolivre(link_original):
    """Aplica o seu ID de afiliado oficial do Mercado Livre ao link do produto."""
    try:
        link_com_afiliado = f"{link_original}?matt_tool={MERCADO_LIVRE_AFFILIATE_ID}"
        return link_com_afiliado
    except Exception as e:
        print(f"Erro ao formatar link do Mercado Livre: {e}")
        return link_original


def buscar_ofertas_afiliados():
    """Retorna a lista unificada de ofertas de todas as lojas com os links de afiliado ativos."""
    
    ofertas_encontradas = [
        {
            "id": "SHOPEE_FONE_01",
            "titulo": "Fone de Ouvido Bluetooth Sem Fio TWS Gamer",
            "preco": 39.90,
            "link": gerar_link_afiliado_shopee("https://shopee.com.br/exemplo-produto"),
            "categoria": "eletronicos",
            "loja": "shopee",
            "imagem": "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=500&q=80",
        },
        {
            "id": "AMZ_ECHO_DOT_5",
            "titulo": "Echo Dot 5ª Geração com Alexa - Som Incrível",
            "preco": 359.10,
            "link": f"https://www.amazon.com.br/dp/B0BZF5K27K?tag={AMAZON_TAG}",
            "categoria": "eletronicos",
            "loja": "amazon",
            "imagem": "https://images.unsplash.com/photo-1543512214-318c7553f230?w=500&q=80",
        },
        {
            "id": "ML_CADEIRA_GAMER_01",
            "titulo": "Cadeira Gamer Ergonômica Reclinável Confortável",
            "preco": 549.00,
            "link": gerar_link_mercadolivre("https://mercadolivre.com.br/sec/exemplo-afiliado"),
            "categoria": "gamer",
            "loja": "mercadolivre",
            "imagem": "https://images.unsplash.com/photo-1598550476439-6847785fcea6?w=500&q=80",
        },
    ]
    
    return ofertas_encontradas