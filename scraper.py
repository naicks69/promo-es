import hashlib
import json
import math
import time
import os
import requests
from dotenv import load_dotenv

import mercadolivre

load_dotenv()

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "")
SHOPEE_API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# Quantos produtos trazer por palavra-chave em cada varredura
PRODUTOS_POR_BUSCA = int(os.getenv("PRODUTOS_POR_BUSCA", "6"))

# Quantos candidatos analisar por busca antes de escolher os que mais rendem comissão
CANDIDATOS_POR_BUSCA = int(os.getenv("CANDIDATOS_POR_BUSCA", "20"))

# Comissão mínima (em %) para um produto da Shopee entrar no site (0 = aceita todos)
COMISSAO_MINIMA = float(os.getenv("COMISSAO_MINIMA", "0"))

# Produtos com menos vendas que isso ficam de fora: quase sem vendas, quase não convertem
VENDAS_MINIMAS = int(os.getenv("VENDAS_MINIMAS", "10"))

# A cada varredura o bot lê a página seguinte dos resultados (1, 2, 3... e volta à 1),
# para trazer produtos diferentes a cada ciclo em vez de repetir sempre os mesmos.
PAGINAS_ROTACAO = int(os.getenv("PAGINAS_ROTACAO", "10"))

# Lojas que o site exibe. A ordem aqui é a ordem dos botões no site.
LOJAS = {
    "amazon": "Amazon",
    "shopee": "Shopee",
    "mercadolivre": "Mercado Livre",
}

# Categorias do site e do WhatsApp (chave interna -> nome exibido)
CATEGORIAS = {
    "casa": "Casa",
    "eletronicos": "Eletrônicos",
    "gamer": "Gamer",
    "moda": "Moda",
}

# O que o bot procura nas lojas e em qual categoria do site cada resultado entra.
# Para acrescentar produtos novos, basta incluir uma linha aqui.
BUSCAS = [
    {"palavra_chave": "garrafa térmica", "categoria": "casa"},
    {"palavra_chave": "organizador cozinha", "categoria": "casa"},
    {"palavra_chave": "luminária led", "categoria": "casa"},
    {"palavra_chave": "fone bluetooth", "categoria": "eletronicos"},
    {"palavra_chave": "smartwatch", "categoria": "eletronicos"},
    {"palavra_chave": "carregador turbo", "categoria": "eletronicos"},
    {"palavra_chave": "mouse gamer", "categoria": "gamer"},
    {"palavra_chave": "headset gamer", "categoria": "gamer"},
    {"palavra_chave": "teclado gamer", "categoria": "gamer"},
    {"palavra_chave": "camiseta masculina", "categoria": "moda"},
    {"palavra_chave": "óculos de sol", "categoria": "moda"},
    {"palavra_chave": "tênis feminino", "categoria": "moda"},
]

_ciclo_atual = 0


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


def taxa_comissao(valor):
    """A Shopee envia a comissão como fração ("0.12" = 12%); aceita também "12"."""
    try:
        taxa = float(valor or 0)
    except (TypeError, ValueError):
        return 0.0
    return taxa / 100 if taxa > 1 else taxa


def pontuacao_comissao(preco, taxa, vendas):
    """Estimativa de quanto o produto rende: comissão em R$ por venda × popularidade.

    Usa o logaritmo das vendas para que um produto muito vendido mas com comissão
    baixa não ganhe de um produto bem vendido com comissão alta.
    """
    return preco * taxa * math.log10(vendas + 10)


def buscar_produtos_shopee(palavra_chave, categoria, limite=PRODUTOS_POR_BUSCA, pagina=1):
    """Procura produtos reais na Shopee e já devolve o link de afiliado de cada um.

    Analisa CANDIDATOS_POR_BUSCA produtos mais vendidos e fica com os `limite` que
    mais devem render em comissão. O campo offerLink da API é o link de afiliado
    que leva direto à página do produto, com a comissão rastreada.
    """
    query = f"""{{
    productOfferV2(keyword: {json.dumps(palavra_chave, ensure_ascii=False)}, sortType: 2, page: {int(pagina)}, limit: {int(max(limite, CANDIDATOS_POR_BUSCA))}) {{
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

        taxa = taxa_comissao(produto.get("commissionRate"))
        vendas = int(produto.get("sales") or 0)
        if taxa * 100 < COMISSAO_MINIMA or vendas < VENDAS_MINIMAS:
            continue

        ofertas.append({
            "id": f"shopee_{item_id}",
            "titulo": titulo,
            "preco": round(preco, 2),
            "link": link_afiliado,
            "categoria": categoria,
            "loja": "shopee",
            "imagem": produto.get("imageUrl") or "",
            "vendas": vendas,
            "pontuacao": round(pontuacao_comissao(preco, taxa, vendas), 4),
        })

    # Fica com os produtos que mais devem render comissão
    ofertas.sort(key=lambda o: o["pontuacao"], reverse=True)
    return ofertas[:limite]


def buscar_ofertas_shopee(pagina):
    """Percorre as buscas da Shopee e devolve as ofertas com link de afiliado."""
    if not credenciais_configuradas():
        print("⚠️ SHOPEE_APP_ID e SHOPEE_SECRET não configurados. Nenhuma oferta da Shopee será buscada.")
        return []

    todas_as_ofertas = []
    ids_vistos = set()

    for busca in BUSCAS:
        ofertas = buscar_produtos_shopee(busca["palavra_chave"], busca["categoria"], pagina=pagina)
        print(f"🔎 Shopee '{busca['palavra_chave']}' (página {pagina}): {len(ofertas)} produtos com link de afiliado")

        for oferta in ofertas:
            # O mesmo produto pode aparecer em duas buscas diferentes
            if oferta["id"] in ids_vistos:
                continue
            ids_vistos.add(oferta["id"])
            todas_as_ofertas.append(oferta)

        time.sleep(1)  # Pausa entre consultas para não estourar o limite da API

    return todas_as_ofertas


def buscar_ofertas_mercadolivre(pagina):
    """Percorre as buscas no Mercado Livre e devolve as promoções com link de afiliado."""
    if not mercadolivre.configurado():
        print("⚠️ ML_CLIENT_ID e ML_CLIENT_SECRET não configurados. Nenhuma oferta do Mercado Livre será buscada.")
        return []
    if not mercadolivre.conectado():
        print("⚠️ Conta do Mercado Livre não conectada. Acesse /mercadolivre no site para conectar.")
        return []

    todas_as_ofertas = []
    ids_vistos = set()
    for busca in BUSCAS:
        ofertas = mercadolivre.buscar_produtos_mercadolivre(
            busca["palavra_chave"], busca["categoria"], PRODUTOS_POR_BUSCA, pagina
        )
        print(f"🔎 Mercado Livre '{busca['palavra_chave']}' (página {pagina}): {len(ofertas)} promoções com link de afiliado")
        for oferta in ofertas:
            if oferta["id"] not in ids_vistos:
                ids_vistos.add(oferta["id"])
                todas_as_ofertas.append(oferta)
        time.sleep(1)
    return todas_as_ofertas


# Cada loja integrada entra nesta lista com a sua função de busca
FONTES_DE_OFERTAS = [
    ("shopee", buscar_ofertas_shopee),
    ("mercadolivre", buscar_ofertas_mercadolivre),
]


def buscar_ofertas_afiliados():
    """Busca as ofertas de todas as lojas integradas, avançando uma página por ciclo."""
    global _ciclo_atual
    pagina = _ciclo_atual % PAGINAS_ROTACAO + 1
    _ciclo_atual += 1

    todas_as_ofertas = []
    for loja, buscar in FONTES_DE_OFERTAS:
        try:
            todas_as_ofertas.extend(buscar(pagina))
        except Exception as e:
            # Uma loja com problema não impede as outras de atualizarem
            print(f"❌ Erro ao buscar ofertas da loja {loja}: {e}")

    print(f"Total de produtos com link de afiliado: {len(todas_as_ofertas)}")
    return todas_as_ofertas
