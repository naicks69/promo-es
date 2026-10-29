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
    """Busca produtos automaticamente na API pública do Mercado Livre com User-Agent."""
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={termo}&limit=5"
    
    # Cabeçalho essencial para evitar bloqueios da API
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    ofertas = []
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        for item in data.get("results", []):
            link_original = item.get("permalink")
            link_afiliado = f"{link_original}?matt_tool={MERCADO_LIVRE_AFFILIATE_ID}" if MERCADO_LIVRE_AFFILIATE_ID else link_original
            
            # Corrige o link da imagem para HTTPS
            imagem_original = item.get("thumbnail", "")
            imagem_segura = imagem_original.replace("http:", "https:") if imagem_original else ""

            ofertas.append({
                "id": f"ML_{item.get('id')}",
                "titulo": item.get("title"),
                "preco": item.get("price"),
                "link": link_afiliado,
                "categoria": "eletronicos",
                "loja": "mercadolivre",
                "imagem": imagem_segura
            })
    except Exception as e:
        print(f"Erro ao buscar Mercado Livre para o termo '{termo}': {e}")
        
    return ofertas


def buscar_ofertas_afiliados():
    """Função central que recolhe novidades de forma autónoma."""
    todas_as_ofertas = []

    # Termos de busca amplos para garantir que a API traga resultados
    termos_busca = ["smartphone", "notebook", "fone", "smartwatch"]
    
    for termo in termos_busca:
        produtos_ml = buscar_ofertas_mercadolivre_auto(termo)
        todas_as_ofertas.extend(produtos_ml)
        time.sleep(1) # Pausa curta entre requisições para evitar limites de taxa

    print(f"Total de ofertas puxadas automaticamente: {len(todas_as_ofertas)}")
    return todas_as_ofertas