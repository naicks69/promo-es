import hashlib
import time
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "")


def gerar_link_afiliado_shopee(link_original):
    """Converte automaticamente qualquer link da Shopee em link rastreável de afiliado."""
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
        short_link = dados.get("data", {}).get("generateShortLink", {}).get("shortLink")
        return short_link if short_link else link_original
    except Exception as e:
        print(f"Erro ao gerar link de afiliado na Shopee: {e}")
        return link_original


def buscar_ofertas_afiliados():
    """Gera e atualiza automaticamente ofertas imperdíveis da Shopee via API."""
    
    # Lista rotativa de produtos em alta com links base da Shopee
    produtos_em_alta = [
        {
            "id": "SHOPEE_FONE_TWS_01",
            "titulo": "Fone de Ouvido Bluetooth Sem Fio TWS Gamer com Microfone",
            "preco": 39.90,
            "link_base": "https://shopee.com.br/search?keyword=fone%20bluetooth%20tws",
            "categoria": "eletronicos",
            "imagem": "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=500&q=80"
        },
        {
            "id": "SHOPEE_SMARTWATCH_02",
            "titulo": "Relógio Smartwatch D20 Y68 Bluetooth Fitness Esportivo",
            "preco": 49.90,
            "link_base": "https://shopee.com.br/search?keyword=smartwatch",
            "categoria": "eletronicos",
            "imagem": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&q=80"
        },
        {
            "id": "SHOPEE_SUPORTE_03",
            "titulo": "Suporte de Mesa Dobrável Articulado para Celular e Tablet",
            "preco": 14.90,
            "link_base": "https://shopee.com.br/search?keyword=suporte%20celular%20mesa",
            "categoria": "casa",
            "imagem": "https://images.unsplash.com/photo-1585060544812-6b45742d762f?w=500&q=80"
        },
        {
            "id": "SHOPEE_RINGLIGHT_04",
            "titulo": "Ring Light de Mesa 16cm com Tripé e Suporte para Celular",
            "preco": 35.00,
            "link_base": "https://shopee.com.br/search?keyword=ring%20light",
            "categoria": "eletronicos",
            "imagem": "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=500&q=80"
        },
        {
            "id": "SHOPEE_GARRAFA_05",
            "titulo": "Garrafa Térmica Inox com Display LED Digital de Temperatura",
            "preco": 32.50,
            "link_base": "https://shopee.com.br/search?keyword=garrafa%20termica%20led",
            "categoria": "casa",
            "imagem": "https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=500&q=80"
        }
    ]

    todas_as_ofertas = []

    for item in produtos_em_alta:
        # Converte o link através da sua API de afiliados da Shopee
        link_afiliado = gerar_link_afiliado_shopee(item["link_base"])
        
        todas_as_ofertas.append({
            "id": item["id"],
            "titulo": item["titulo"],
            "preco": item["preco"],
            "link": link_afiliado,
            "categoria": item["categoria"],
            "loja": "shopee",
            "imagem": item["imagem"]
        })
        time.sleep(0.3)

    print(f"Total de ofertas processadas: {len(todas_as_ofertas)}")
    return todas_as_ofertas