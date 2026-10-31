import hashlib
import json
import math
import re
import time
import os
import unicodedata
from urllib.parse import quote_plus
import requests
from dotenv import load_dotenv

import aliexpress
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

# A Shopee fica com os itens mais básicos e baratos; acima deste preço o produto
# fica para o Mercado Livre (só vale nas varreduras, não na busca do cliente)
SHOPEE_PRECO_MAXIMO = float(os.getenv("SHOPEE_PRECO_MAXIMO", "100"))

# A cada varredura o bot lê a página seguinte dos resultados (1, 2, 3... e volta à 1),
# para trazer produtos diferentes a cada ciclo em vez de repetir sempre os mesmos.
PAGINAS_ROTACAO = int(os.getenv("PAGINAS_ROTACAO", "10"))

# Tag de afiliado da Amazon (Associates). Enquanto a Creators API não é liberada,
# é usada no link "Ver resultados na Amazon" da busca do site.
AMAZON_TAG = os.getenv("AMAZON_TAG", "vidinhaoferta-20")

# Lojas que o site exibe. A ordem aqui é a ordem dos botões no site.
LOJAS = {
    "amazon": "Amazon",
    "shopee": "Shopee",
    "mercadolivre": "Mercado Livre",
    "aliexpress": "AliExpress",
}

# Categorias do site e do WhatsApp (chave interna -> nome exibido)
CATEGORIAS = {
    "casa": "Casa",
    "cozinha": "Cozinha",
    "eletronicos": "Eletrônicos",
    "gamer": "Gamer",
    "moda": "Moda",
    "beleza": "Beleza",
    "esporte": "Esporte",
    "infantil": "Infantil",
    "pet": "Pet",
}

# O que o bot procura na Shopee e no AliExpress (itens mais básicos) e em qual
# categoria do site cada resultado entra. Cada varredura usa só uma parte da lista,
# em rodízio, para variar os produtos sem gastar mais chamadas das APIs.
# Para acrescentar produtos novos, basta incluir uma linha aqui.
BUSCAS = [
    {"palavra_chave": "garrafa térmica", "categoria": "casa"},
    {"palavra_chave": "luminária led", "categoria": "casa"},
    {"palavra_chave": "organizador de armário", "categoria": "casa"},
    {"palavra_chave": "jogo de cama", "categoria": "casa"},
    {"palavra_chave": "toalha de banho", "categoria": "casa"},
    {"palavra_chave": "organizador cozinha", "categoria": "cozinha"},
    {"palavra_chave": "potes herméticos", "categoria": "cozinha"},
    {"palavra_chave": "utensílios de silicone", "categoria": "cozinha"},
    {"palavra_chave": "mixer de mão", "categoria": "cozinha"},
    {"palavra_chave": "fone bluetooth", "categoria": "eletronicos"},
    {"palavra_chave": "smartwatch", "categoria": "eletronicos"},
    {"palavra_chave": "carregador turbo", "categoria": "eletronicos"},
    {"palavra_chave": "caixa de som portátil", "categoria": "eletronicos"},
    {"palavra_chave": "suporte de celular", "categoria": "eletronicos"},
    {"palavra_chave": "mouse gamer", "categoria": "gamer"},
    {"palavra_chave": "headset gamer", "categoria": "gamer"},
    {"palavra_chave": "teclado gamer", "categoria": "gamer"},
    {"palavra_chave": "mousepad gamer", "categoria": "gamer"},
    {"palavra_chave": "camiseta masculina", "categoria": "moda"},
    {"palavra_chave": "óculos de sol", "categoria": "moda"},
    {"palavra_chave": "tênis feminino", "categoria": "moda"},
    {"palavra_chave": "bolsa feminina", "categoria": "moda"},
    {"palavra_chave": "relógio feminino", "categoria": "moda"},
    {"palavra_chave": "skincare", "categoria": "beleza"},
    {"palavra_chave": "maquiagem", "categoria": "beleza"},
    {"palavra_chave": "secador de cabelo", "categoria": "beleza"},
    {"palavra_chave": "perfume", "categoria": "beleza"},
    {"palavra_chave": "garrafa de academia", "categoria": "esporte"},
    {"palavra_chave": "elástico de exercício", "categoria": "esporte"},
    {"palavra_chave": "tapete de yoga", "categoria": "esporte"},
    {"palavra_chave": "roupa de academia", "categoria": "esporte"},
    {"palavra_chave": "brinquedo educativo", "categoria": "infantil"},
    {"palavra_chave": "roupa infantil", "categoria": "infantil"},
    {"palavra_chave": "mochila escolar", "categoria": "infantil"},
    {"palavra_chave": "brinquedo para cachorro", "categoria": "pet"},
    {"palavra_chave": "cama pet", "categoria": "pet"},
    {"palavra_chave": "arranhador gato", "categoria": "pet"},
]

# No Mercado Livre o bot procura produtos de mais qualidade (a partir de ML_PRECO_MINIMO),
# sempre entre os mais vendidos de cada categoria. Para acrescentar, inclua uma linha aqui.
BUSCAS_MERCADOLIVRE = [
    {"palavra_chave": "aspirador de pó", "categoria": "casa"},
    {"palavra_chave": "ventilador", "categoria": "casa"},
    {"palavra_chave": "jogo de cama queen", "categoria": "casa"},
    {"palavra_chave": "air fryer", "categoria": "cozinha"},
    {"palavra_chave": "cafeteira", "categoria": "cozinha"},
    {"palavra_chave": "jogo de panelas", "categoria": "cozinha"},
    {"palavra_chave": "liquidificador", "categoria": "cozinha"},
    {"palavra_chave": "smartphone", "categoria": "eletronicos"},
    {"palavra_chave": "smart tv", "categoria": "eletronicos"},
    {"palavra_chave": "fone de ouvido jbl", "categoria": "eletronicos"},
    {"palavra_chave": "caixa de som bluetooth", "categoria": "eletronicos"},
    {"palavra_chave": "notebook", "categoria": "eletronicos"},
    {"palavra_chave": "console videogame", "categoria": "gamer"},
    {"palavra_chave": "controle ps5", "categoria": "gamer"},
    {"palavra_chave": "monitor gamer", "categoria": "gamer"},
    {"palavra_chave": "cadeira gamer", "categoria": "gamer"},
    {"palavra_chave": "tênis esportivo", "categoria": "moda"},
    {"palavra_chave": "relógio masculino", "categoria": "moda"},
    {"palavra_chave": "mochila", "categoria": "moda"},
    {"palavra_chave": "perfume importado", "categoria": "beleza"},
    {"palavra_chave": "chapinha", "categoria": "beleza"},
    {"palavra_chave": "barbeador elétrico", "categoria": "beleza"},
    {"palavra_chave": "bicicleta", "categoria": "esporte"},
    {"palavra_chave": "esteira ergométrica", "categoria": "esporte"},
    {"palavra_chave": "halteres", "categoria": "esporte"},
    {"palavra_chave": "carrinho de bebê", "categoria": "infantil"},
    {"palavra_chave": "lego", "categoria": "infantil"},
    {"palavra_chave": "ração para cachorro", "categoria": "pet"},
    {"palavra_chave": "caixa de transporte pet", "categoria": "pet"},
]

# Quantas palavras-chave cada varredura usa (em rodízio). Com a lista inteira passando
# a cada poucas varreduras, os produtos variam sem gastar mais chamadas das APIs.
BUSCAS_POR_VARREDURA = int(os.getenv("BUSCAS_POR_VARREDURA", "12"))


def intercalar_por_categoria(buscas):
    """Ordena alternando as categorias (casa, cozinha, eletrônicos...) para cada rodada ser variada."""
    grupos = {}
    for busca in buscas:
        grupos.setdefault(busca["categoria"], []).append(busca)
    intercaladas = []
    while any(grupos.values()):
        for categoria in list(grupos):
            if grupos[categoria]:
                intercaladas.append(grupos[categoria].pop(0))
    return intercaladas


def rodada(buscas, ciclo):
    """Palavras-chave e página desta varredura.

    Cada varredura pega as próximas BUSCAS_POR_VARREDURA palavras da lista; quando a lista
    inteira já passou, avança para a página seguinte dos resultados.
    """
    ordem = intercalar_por_categoria(buscas)
    quantidade = min(BUSCAS_POR_VARREDURA, len(ordem))
    rodadas_por_volta = max(1, -(-len(ordem) // quantidade))
    inicio = (ciclo % rodadas_por_volta) * quantidade
    da_vez = (ordem + ordem)[inicio:inicio + quantidade]
    pagina = (ciclo // rodadas_por_volta) % PAGINAS_ROTACAO + 1
    return da_vez, pagina


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


def buscar_produtos_shopee(palavra_chave, categoria, limite=PRODUTOS_POR_BUSCA, pagina=1,
                           ordem=2, escolher_por_comissao=True):
    """Procura produtos reais na Shopee e já devolve o link de afiliado de cada um.

    Nas varreduras (escolher_por_comissao=True), analisa CANDIDATOS_POR_BUSCA produtos
    mais vendidos e fica com os `limite` que mais devem render em comissão. Na busca do
    cliente, mantém a ordem de relevância da Shopee (ordem=1). O campo offerLink da API
    é o link de afiliado que leva direto à página do produto, com a comissão rastreada.
    """
    query = f"""{{
    productOfferV2(keyword: {json.dumps(palavra_chave, ensure_ascii=False)}, sortType: {int(ordem)}, page: {int(pagina)}, limit: {int(max(limite, CANDIDATOS_POR_BUSCA))}) {{
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
        if taxa * 100 < COMISSAO_MINIMA:
            continue
        if escolher_por_comissao and (vendas < VENDAS_MINIMAS or preco > SHOPEE_PRECO_MAXIMO):
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
    if escolher_por_comissao:
        ofertas.sort(key=lambda o: o["pontuacao"], reverse=True)
    return ofertas[:limite]


def buscar_ofertas_shopee(ciclo):
    """Percorre as buscas da Shopee e devolve as ofertas com link de afiliado."""
    if not credenciais_configuradas():
        print("⚠️ SHOPEE_APP_ID e SHOPEE_SECRET não configurados. Nenhuma oferta da Shopee será buscada.")
        return []

    todas_as_ofertas = []
    ids_vistos = set()

    buscas, pagina = rodada(BUSCAS, ciclo)
    for busca in buscas:
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


def buscar_ofertas_mercadolivre(ciclo):
    """Pega os mais vendidos do Mercado Livre de cada categoria do site, com link de afiliado."""
    if not mercadolivre.configurado():
        print("⚠️ ML_CLIENT_ID e ML_CLIENT_SECRET não configurados. Nenhuma oferta do Mercado Livre será buscada.")
        return []

    buscas, pagina = rodada(BUSCAS_MERCADOLIVRE, ciclo)
    palavras_por_categoria = {}
    for busca in buscas:
        palavras_por_categoria.setdefault(busca["categoria"], []).append(busca["palavra_chave"])

    todas_as_ofertas = []
    total_categorias_ml = 0
    for categoria, palavras in palavras_por_categoria.items():
        # Mesma quantidade de produtos por categoria que a Shopee traz
        ofertas, categorias_ml = mercadolivre.buscar_ofertas_da_categoria(
            categoria, palavras, PRODUTOS_POR_BUSCA * len(palavras), pagina
        )
        total_categorias_ml += categorias_ml
        print(f"🔎 Mercado Livre '{categoria}' (página {pagina}): {len(ofertas)} produtos com link de afiliado")
        todas_as_ofertas.extend(ofertas)

    mercadolivre.resumir_varredura(total_categorias_ml, len(todas_as_ofertas))
    return todas_as_ofertas


def link_busca_amazon(termo):
    """Página de resultados da Amazon com a tag de afiliado (vale comissão)."""
    return f"https://www.amazon.com.br/s?k={quote_plus(termo)}&tag={quote_plus(AMAZON_TAG)}"


def pesquisar_nas_lojas(termo, limite=12):
    """Busca do cliente: produtos que ele procura, mesmo sem promoção, com link de afiliado."""
    resultados_por_loja = []
    if credenciais_configuradas():
        resultados_por_loja.append(
            buscar_produtos_shopee(termo, "busca", limite, ordem=1, escolher_por_comissao=False)
        )
    if mercadolivre.configurado():
        resultados_por_loja.append(
            mercadolivre.buscar_produtos_mercadolivre(termo, "busca", limite, so_promocoes=False)
        )
    if aliexpress.configurado():
        try:
            resultados_por_loja.append(
                aliexpress.buscar_produtos_aliexpress(termo, "busca", limite, so_promocoes=False)
            )
        except Exception as e:
            # As outras lojas continuam aparecendo
            print(f"AliExpress: erro na busca do cliente '{termo}': {e}")

    # Intercala as lojas (1 da Shopee, 1 do Mercado Livre...) para o cliente comparar
    intercalados = []
    for posicao in range(limite):
        for resultados in resultados_por_loja:
            if posicao < len(resultados):
                intercalados.append(resultados[posicao])
    return intercalados[:limite]


def buscar_ofertas_aliexpress(ciclo):
    """Percorre as buscas no AliExpress e devolve as ofertas com link de afiliado."""
    if not aliexpress.configurado():
        print("⚠️ ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET e ALIEXPRESS_TRACKING_ID não configurados.")
        return []

    todas_as_ofertas = []
    ids_vistos = set()
    erros = []
    buscas, pagina = rodada(BUSCAS, ciclo)
    for busca in buscas:
        try:
            ofertas = aliexpress.buscar_produtos_aliexpress(
                busca["palavra_chave"], busca["categoria"], PRODUTOS_POR_BUSCA, pagina
            )
        except Exception as e:
            erros.append(f"'{busca['palavra_chave']}': {e}")
            ofertas = []
        print(f"🔎 AliExpress '{busca['palavra_chave']}' (página {pagina}): {len(ofertas)} produtos com link de afiliado")
        for oferta in ofertas:
            if oferta["id"] not in ids_vistos:
                ids_vistos.add(oferta["id"])
                todas_as_ofertas.append(oferta)
        time.sleep(1)

    resumo = f"última varredura: {len(buscas)} buscas, {len(todas_as_ofertas)} ofertas com link de afiliado."
    if erros:
        resumo += f" {len(erros)} busca(s) com erro. Primeiro erro: {erros[0]}"
    aliexpress.registrar_diagnostico(resumo)
    return todas_as_ofertas


# Cada loja integrada entra nesta lista com a sua função de busca
FONTES_DE_OFERTAS = [
    ("shopee", buscar_ofertas_shopee),
    ("mercadolivre", buscar_ofertas_mercadolivre),
    ("aliexpress", buscar_ofertas_aliexpress),
]


# Palavras que não ajudam a distinguir um produto de outro
PALAVRAS_VAZIAS = {
    "de", "da", "do", "das", "dos", "com", "para", "e", "em", "a", "o", "as", "os", "kit",
    "novo", "nova", "original", "promocao", "oferta", "frete", "gratis", "envio", "imediato",
}


def palavras_do_titulo(titulo):
    """Primeiras palavras significativas do título, sem acento e em minúsculas."""
    sem_acento = unicodedata.normalize("NFD", titulo or "").encode("ascii", "ignore").decode().lower()
    # "500 ml" e "500ml" viram a mesma palavra
    sem_acento = re.sub(r"(\d+)\s+(ml|l|g|kg|cm|mm|m|w|v|gb|tb|mah|pol|polegadas)\b", r"\1\2", sem_acento)
    palavras = re.findall(r"[a-z0-9]+", sem_acento)
    return set([p for p in palavras if len(p) > 1 and p not in PALAVRAS_VAZIAS][:8])


def parecidos(palavras_a, palavras_b):
    """Títulos quase iguais: 60% ou mais das palavras significativas em comum."""
    if not palavras_a or not palavras_b:
        return False
    return len(palavras_a & palavras_b) / len(palavras_a | palavras_b) >= 0.6


def filtrar_repetidos(ofertas, ativas, recentes):
    """Tira da varredura os produtos que deixariam o site repetitivo.

    - produto publicado há pouco tempo (está em `recentes`) não volta logo depois de sair;
    - produto quase igual (título parecido) a outro que está no site, que passou por ele
      há pouco tempo, ou que já entrou nesta mesma varredura, fica de fora.
    As ofertas que já estão no site seguem normalmente (só têm preço e link atualizados).
    """
    ids_ativos = {id_ for id_, _ in ativas}
    ids_recentes = {id_ for id_, _ in recentes}
    titulos = [palavras_do_titulo(titulo) for _, titulo in list(ativas) + list(recentes)]
    aceitas, descartadas = [], 0
    for oferta in ofertas:
        if oferta["id"] in ids_ativos:
            aceitas.append(oferta)
            continue
        if oferta["id"] in ids_recentes:
            descartadas += 1
            continue
        palavras = palavras_do_titulo(oferta["titulo"])
        if any(parecidos(palavras, outras) for outras in titulos):
            descartadas += 1
            continue
        titulos.append(palavras)
        ids_ativos.add(oferta["id"])
        aceitas.append(oferta)
    return aceitas, descartadas


def buscar_ofertas_afiliados():
    """Busca as ofertas de todas as lojas integradas, com palavras-chave em rodízio."""
    global _ciclo_atual
    ciclo = _ciclo_atual
    _ciclo_atual += 1

    todas_as_ofertas = []
    for loja, buscar in FONTES_DE_OFERTAS:
        try:
            todas_as_ofertas.extend(buscar(ciclo))
        except Exception as e:
            # Uma loja com problema não impede as outras de atualizarem
            print(f"❌ Erro ao buscar ofertas da loja {loja}: {e}")

    print(f"Total de produtos com link de afiliado: {len(todas_as_ofertas)}")
    return todas_as_ofertas
