import hashlib
import time
import os
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "")


def gerar_link_afiliado_shopee(link_original):
    """Converte automaticamente qualquer link direto da Shopee em link rastreável de afiliado."""
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
    """Converte automaticamente links diretos de produtos da Shopee em ofertas para o site."""
    
    # Lista de produtos com LINKS DIRETOS REAIS da Shopee (contendo o padrão i.loja.produto)
    produtos_diretos = [
        {
            "id": "PROD_GARRAFA_LED",
            "titulo": "Garrafa Térmica Aço Inox com Display LED de Temperatura",
            "preco": 32.50,
            # Link direto de produto real da Shopee
            "link_ original": "https://shopee.com.br/Garrafa-Térmica-Aço-Inox-com-Display-LED-i.312845678.2259812345",
            "categoria": "casa",
            "imagem": "https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=500&q=80"
        },
        {
            "id": "PROD_FONE_TWS",
            "titulo": "Fone de Ouvido Bluetooth Sem Fio TWS Gamer",
            "preco": 39.90,
            "link_original": "https://shopee.com.br/Fone-de-Ouvido-Bluetooth-TWS-i.154879264.1894561230",
            "categoria": "eletronicos",
            "imagem": "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=500&q=80"
        },
        {
            "id": "PROD_SMARTWATCH",
            "titulo": "Relógio Smartwatch D20 Y68 Bluetooth Fitness",
            "preco": 49.90,
            "link_original": "https://shopee.com.br/Relogio-Smartwatch-D20-Y68-i.298741365.1548963210",
            "categoria": "eletronicos",
            "imagem": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&q=80"
        },
        {
            "id": "PROD_SUPORTE_CELULAR",
            "titulo": "Suporte de Mesa Dobrável para Celular e Tablet",
            "preco": 14.90,
            "link_original": "https://shopee.com.br/Suporte-De-Mesa-Dobravel-Celular-i.412365897.2314569870",
            "categoria": "casa",
            "imagem": "https://images.unsplash.com/photo-1585060544812-6b45742d762f?w=500&q=80"
        }
    ]

    todas_as_ofertas = []

    for item in produtos_diretos:
        # Pega o link original do produto e converte usando a API de afiliados da Shopee
        link_afiliado = gerar_link_afiliado_shopee(item["link_original"])
        
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

    print(f"Total de produtos diretos processados: {len(todas_as_ofertas)}")
    return todas_as_ofertas