# Radar de Ofertas

Site de promoções da Amazon, Shopee e Mercado Livre com links de afiliado e envio
das ofertas pelo WhatsApp.

## Como funciona

- **A cada 5 minutos** o servidor busca ofertas novas (cada busca lê a página
  seguinte dos resultados, para variar os produtos) e salva no banco.
- **Cada oferta fica no site por até 4 horas** depois de publicada; depois disso
  ela sai do site e do banco.
- **A cada 10 minutos**, das 8h às 22h, o bot de WhatsApp posta nos grupos
  ativados até 3 ofertas novas do site (foto, preço e link de afiliado). Quem se
  inscreve no privado recebe só as categorias que escolheu. Nenhuma oferta se repete
  para o mesmo grupo ou pessoa.

As variáveis de ambiente estão explicadas em `.env.example`.

## Lojas

| Loja | Situação |
| --- | --- |
| Shopee | Integrada (API de Afiliados) |
| Mercado Livre | Integrado (busca pela API + link de afiliado `matt_word`/`matt_tool`). Precisa do aplicativo e da conexão da conta, veja abaixo |
| Amazon | Aparece no site como "em breve" até a Creators API ser liberada (tag de afiliado: `vidinhaoferta-20`) |

### Como as ofertas são escolhidas para render mais comissão

- **Shopee:** cada busca analisa 20 produtos mais vendidos e fica com os 6 que mais
  devem render: preço × % de comissão × popularidade. Produtos com menos de 10
  vendas ficam de fora.
- **Mercado Livre:** só entram produtos com pelo menos 10% de desconto sobre o preço
  anterior, ordenados pelo mesmo cálculo (com uma comissão estimada de 5%, porque o
  Mercado Livre não informa a comissão de cada produto).
- **WhatsApp:** o bot manda primeiro as ofertas com maior pontuação.

## Busca do cliente nas lojas

Quando o cliente digita algo na busca do site, aparece o botão **Buscar nas lojas**:
o site procura o termo na Shopee e no Mercado Livre (mesmo produtos sem promoção) e
mostra os resultados com link de afiliado, mais um botão para ver os resultados na
Amazon com a tag `vidinhaoferta-20`. Rota: `GET /api/buscar?q=termo` (resultados
guardados por 15 min, até 8 buscas por minuto por visitante).

## Conectar o Mercado Livre

1. Em **developers.mercadolivre.com.br**, entre com a conta do Mercado Livre e crie
   um aplicativo (Minhas aplicações > Criar nova aplicação).
2. No campo **URIs de redirect**, cole o endereço mostrado na página
   `/mercadolivre?senha=SUA_SENHA` do site (é `https://SEU-SITE.onrender.com/mercadolivre/retorno`).
3. Nas permissões, marque leitura e **offline_access** (para a conexão não expirar).
4. Copie o **Client ID** e o **Client Secret** e cadastre no Render como
   `ML_CLIENT_ID` e `ML_CLIENT_SECRET`.
5. Abra `/mercadolivre?senha=SUA_SENHA` e clique em **Conectar Mercado Livre**.

Se o Mercado Livre mostrar "não foi possível conectar o aplicativo à sua conta":
confira se a URI de redirect do aplicativo é idêntica à mostrada na página, se você
entrou com a conta principal (não um colaborador) e, se o PKCE estiver desligado no
aplicativo, use o link **Conectar sem PKCE** da mesma página.

Para integrar uma loja nova, crie a função de busca em `scraper.py` e inclua-a em
`FONTES_DE_OFERTAS`. Ela deve devolver ofertas com `link` de afiliado e `loja`
igual a `amazon` ou `mercadolivre`.

## Bot de WhatsApp (pasta `bot/`)

O bot usa o número **+55 11 91003-2144** como um "aparelho conectado" do WhatsApp
(como o WhatsApp Web). O celular com esse número continua funcionando normalmente.

O bot é quem procura o site: a cada minuto ele pergunta se há ofertas para enviar,
então pode rodar em qualquer computador, sem precisar de endereço público.

### 1. Combinar a senha entre o site e o bot

No Render, em **Environment**, crie `WHATSAPP_BOT_TOKEN` com uma senha forte
(ex.: uma sequência longa de letras e números). Use a mesma senha no passo 3.

### 2. Instalar

No computador que vai deixar o bot ligado (precisa do Node.js 20 ou mais novo,
em nodejs.org):

```bash
cd bot
npm install
```

### 3. Configurar

Copie `bot/.env.example` para `bot/.env` e preencha `SITE_URL` (endereço do site
no Render) e `WHATSAPP_BOT_TOKEN` (a senha do passo 1).

### 4. Ligar e conectar o número

```bash
npm start
```

Na primeira vez aparece um **código de 8 letras**. No celular do número do bot,
abra o WhatsApp > Configurações > Aparelhos conectados > Conectar aparelho >
**Conectar com número de telefone** e digite o código. A conexão fica salva na
pasta `bot/sessao` (não apague e não compartilhe essa pasta).

### 5. Ativar o grupo de promoções

1. No celular do bot, crie o grupo (ex.: "Radar de Ofertas - Promoções"). O número
   do bot precisa ser administrador, e é recomendado deixar só administradores
   enviarem mensagens (Configurações do grupo > Enviar mensagens > Só admins).
2. Escreva **`!ativar`** no grupo, pelo celular do bot.
3. O bot confirma no grupo e envia o link de convite para o site, que passa a
   mostrar o botão "Entrar no grupo de promoções" sozinho.

Para parar de postar em um grupo, escreva **`!desativar`** nele.

### Mensagens que o bot entende no privado

- `Quero receber ofertas de Casa e Gamer`: inscreve nas categorias citadas
- `Quero receber` ou `Tudo`: inscreve em todas as categorias
- `SAIR`, `PARAR` ou `CANCELAR`: cancela a inscrição
- qualquer outra mensagem: responde com o menu e o link do grupo

### Cuidados

- O bot precisa ficar ligado o tempo todo para postar. Se o computador desligar,
  as ofertas param até ele voltar (nada se perde: ele continua de onde parou).
- O WhatsApp não tem API oficial para isso. O bot usa a biblioteca Baileys, e o
  WhatsApp pode bloquear números que mandam muitas mensagens. Por isso o bot faz
  pausas entre envios, não envia à noite e manda poucas ofertas por rodada.
- O celular do bot precisa abrir o WhatsApp pelo menos uma vez a cada 14 dias,
  senão o WhatsApp desconecta os aparelhos conectados.

### Rotas do site usadas pelo bot

Todas exigem o cabeçalho `X-Bot-Token` com a senha de `WHATSAPP_BOT_TOKEN`.

| Rota | Para que serve |
| --- | --- |
| `GET /api/whatsapp/pendentes` | ofertas que cada grupo ou pessoa deve receber agora |
| `POST /api/whatsapp/confirmar` | marca as ofertas enviadas, para não repetir |
| `POST /api/whatsapp/mensagem` | mensagem recebida no privado; devolve a resposta |
| `POST /api/whatsapp/grupos` | ativa um grupo (comando `!ativar`) |
| `POST /api/whatsapp/grupos/remover` | desativa um grupo (comando `!desativar`) |
