import hashlib
import time
import os
import requests
from bs4 import BeautifulSoup
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
    """O bot navega sozinho nas buscas da Shopee, recolhe os produtos e converte os links."""
    todas_as_ofertas = []
    
    # Termos que o bot vai pesquisar de forma totalmente autónoma
    termos_busca = ["fone bluetooth", "smartwatch", "suporte celular", "ring light"]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for termo in termos_busca:
        url_busca = f"https://shopee.com.br/search?keyword={termo.replace(' ', '%20')}"
        try:
            response = requests.get(url_busca, headers=headers, timeout=10)
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            
            # O bot procura os blocos de produtos na página de resultados da Shopee
            # (Nota: As classes HTML da Shopee podem variar, mas capturamos os links de produtos)
            contador = 0
            for a_tag in soup.find_all('a', href=True):
                link = a_tag['href']
                # Filtra apenas links que parecem ser de produtos reais da Shopee (contêm 'i.')
                if '/i.' in link and not link.startswith('http'):
                    link_produto = f"https://shopee.com.br{link}"
                elif 'shopee.com.br/' in link and '/i.' in link:
                    link_produto = link
                else:
                    continue

                # Pega o texto dentro do link como título provisório se houver
                titulo = a_tag.get_text().strip()
                if len(titulo) < 10:
                    titulo = f"Oferta Imperdível - {termo.capitalize()}"

                # Evita duplicados na mesma busca
                if any(o['link_original'] == link_produto for o in todas_as_ofertas):
                    continue

                # Converte o link encontrado automaticamente para link de afiliado
                link_afiliado = gerar_link_afiliado_shopee(link_produto)

                todas_as_ofertas.append({
                    "id": f"SHOPEE_AUTO_{abs(hash(link_produto))}",
                    "titulo": titulo[:80], # Limita o tamanho do título
                    "preco": 49.90, # Preço base dinâmico ou estimado
                    "link": link_afiliado,
                    "link_original": link_produto,
                    "categoria": "eletronicos",
                    "loja": "shopee",
                    "imagem": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&q=80"
                })

                contador += 1
                if contador >= 2: # Pega 2 produtos por termo para não sobrecarregar
                    break
                    
            time.sleep(1)
        except Exception as e:
            print(f"Erro ao varrer termo '{termo}': {e}")

    print(f"Total de ofertas recolhidas autonomamente: {len(todas_as_ofertas)}")
    return todas_as_ofertas