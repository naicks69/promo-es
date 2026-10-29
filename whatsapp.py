import unicodedata
from scraper import CATEGORIAS, LOJAS

# Palavras que fazem a pessoa parar de receber ofertas
PALAVRAS_SAIR = ("sair", "parar", "pare", "cancelar", "descadastrar")

# Palavras que indicam que a pessoa quer receber ofertas
PALAVRAS_ENTRAR = ("quero", "receber", "ofertas", "oferta", "promo", "cadastrar", "entrar", "sim")


def normalizar(texto):
    """Minúsculas e sem acentos, para comparar "Eletrônicos" com "eletronicos"."""
    sem_acento = unicodedata.normalize("NFD", texto or "")
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return sem_acento.lower()


def categorias_no_texto(texto):
    texto = normalizar(texto)
    if "tudo" in texto or "todas" in texto:
        return ["todas"]
    return [chave for chave, nome in CATEGORIAS.items() if normalizar(nome) in texto or chave in texto]


def nomes_das_categorias(categorias):
    if "todas" in categorias:
        return "todas as categorias"
    nomes = [CATEGORIAS.get(c, c) for c in categorias]
    return nomes[0] if len(nomes) == 1 else f"{', '.join(nomes[:-1])} e {nomes[-1]}"


def texto_menu(intervalo_minutos):
    opcoes = "\n".join(f"• {nome}" for nome in CATEGORIAS.values())
    return (
        "👋 Olá! Eu sou o bot do *Radar de Ofertas*.\n\n"
        f"Envio promoções da Amazon, Shopee e Mercado Livre a cada {intervalo_minutos} minutos.\n\n"
        "Para receber, responda com as categorias que você quer, por exemplo:\n"
        "*Quero receber Casa e Gamer*\n\n"
        f"Categorias:\n{opcoes}\n• Tudo\n\n"
        "Para parar de receber, envie *SAIR*."
    )


def interpretar_mensagem(texto, intervalo_minutos):
    """Decide o que fazer com uma mensagem recebida pelo bot.

    Devolve (acao, categorias, resposta), onde acao é "inscrever", "sair" ou "menu".
    """
    normalizado = normalizar(texto)
    palavras = normalizado.replace(",", " ").replace(".", " ").replace("!", " ").split()

    if any(p in palavras for p in PALAVRAS_SAIR):
        resposta = (
            "✅ Pronto, você não vai mais receber ofertas.\n"
            "Se mudar de ideia, é só mandar *QUERO RECEBER OFERTAS*."
        )
        return "sair", [], resposta

    categorias = categorias_no_texto(texto)
    quer_entrar = categorias or any(p in palavras for p in PALAVRAS_ENTRAR)
    if not quer_entrar:
        return "menu", [], texto_menu(intervalo_minutos)

    if not categorias:
        categorias = ["todas"]
    resposta = (
        f"✅ Inscrição feita! Você vai receber ofertas de *{nomes_das_categorias(categorias)}* "
        f"a cada {intervalo_minutos} minutos.\n\n"
        "Para mudar, mande outras categorias (ex.: *Moda e Casa*). Para parar, envie *SAIR*."
    )
    return "inscrever", categorias, resposta


def formatar_preco(preco):
    inteiro, centavos = f"{float(preco):.2f}".split(".")
    return f"R$ {int(inteiro):,}".replace(",", ".") + f",{centavos}"


def montar_mensagem_ofertas(ofertas):
    """Junta várias ofertas numa única mensagem, para não lotar a conversa."""
    blocos = []
    for oferta in ofertas:
        loja = LOJAS.get(oferta.get("loja"), oferta.get("loja") or "")
        blocos.append(
            f"📦 *{oferta['titulo']}*\n"
            f"💰 *{formatar_preco(oferta['preco'])}* na {loja}\n"
            f"👉 {oferta['link']}"
        )
    titulo = "🔥 *Nova oferta no Radar!*" if len(ofertas) == 1 else f"🔥 *{len(ofertas)} novas ofertas no Radar!*"
    return f"{titulo}\n\n" + "\n\n".join(blocos) + "\n\n_Para parar de receber, envie SAIR._"
