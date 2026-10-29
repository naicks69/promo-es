# Radar de Ofertas

Site de promoções da Amazon, Shopee e Mercado Livre com links de afiliado e envio
das ofertas pelo WhatsApp.

## Como funciona

- **A cada 5 minutos** o servidor busca ofertas novas (cada busca lê a página
  seguinte dos resultados, para variar os produtos) e salva no banco.
- **Cada oferta fica no site por até 4 horas** depois de publicada; depois disso
  ela sai do site e do banco.
- **A cada 30 minutos**, das 8h às 22h, cada pessoa inscrita no WhatsApp recebe
  uma mensagem com até 3 ofertas novas das categorias que escolheu, sem repetir
  ofertas que já recebeu.

As variáveis de ambiente estão explicadas em `.env.example`.

## Lojas

| Loja | Situação |
| --- | --- |
| Shopee | Integrada (API de Afiliados) |
| Amazon | Aparece no site como "em breve" até a integração ser feita |
| Mercado Livre | Aparece no site como "em breve" até a integração ser feita |

Para integrar uma loja nova, crie a função de busca em `scraper.py` e inclua-a em
`FONTES_DE_OFERTAS`. Ela deve devolver ofertas com `link` de afiliado e `loja`
igual a `amazon` ou `mercadolivre`.

## Ligando o bot de WhatsApp ao site

O botão "Receber no WhatsApp" do site abre uma conversa com o bot já com a mensagem
escrita, por exemplo: *Quero receber ofertas de Casa e Gamer*. Para o bot cadastrar
a pessoa, ele precisa repassar cada mensagem recebida para o site e responder com o
texto que o site devolver.

**Rota:** `POST /api/whatsapp/mensagem`
**Cabeçalho:** `X-Bot-Token: <mesmo valor de WHATSAPP_BOT_TOKEN>`
**Corpo:** `{"numero": "<id do WhatsApp>", "texto": "<mensagem recebida>"}`
**Resposta:** `{"acao": "inscrever" | "sair" | "menu", "resposta": "<texto para enviar de volta>"}`

O `numero` enviado aqui é o mesmo que o site usa depois em `/send-promo`, então use
o formato que o seu bot já aceita para enviar mensagens.

Exemplo com Baileys (Node.js):

```js
sock.ev.on('messages.upsert', async ({ messages }) => {
  for (const msg of messages) {
    const numero = msg.key.remoteJid;
    if (msg.key.fromMe || !msg.message || numero.endsWith('@g.us')) continue; // ignora grupos

    const texto = msg.message.conversation || msg.message.extendedTextMessage?.text || '';
    if (!texto) continue;

    const r = await fetch(`${process.env.SITE_URL}/api/whatsapp/mensagem`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Bot-Token': process.env.WHATSAPP_BOT_TOKEN,
      },
      body: JSON.stringify({ numero, texto }),
    });
    const { resposta } = await r.json();
    if (resposta) await sock.sendMessage(numero, { text: resposta });
  }
});
```

O que o bot entende:

- `Quero receber ofertas de Casa e Gamer`: inscreve nas categorias citadas
- `Quero receber` ou `Tudo`: inscreve em todas as categorias
- `SAIR`, `PARAR` ou `CANCELAR`: cancela a inscrição
- qualquer outra mensagem: responde com o menu de opções
