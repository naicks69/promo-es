// Bot de WhatsApp do Radar de Ofertas.
//
// - Posta nos grupos ativados (e no privado de quem se inscreveu) as ofertas do site,
//   consultando o site a cada minuto; o site decide o que enviar e quando (10 em 10 min).
// - Repassa ao site as mensagens recebidas no privado e responde com o texto devolvido.
// - No grupo, o dono do número escreve !ativar para o bot começar a postar ali.

import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import pino from 'pino';
import makeWASocket, {
    Browsers,
    DisconnectReason,
    fetchLatestBaileysVersion,
    useMultiFileAuthState,
} from '@whiskeysockets/baileys';

carregarArquivoEnv();

const SITE_URL = (process.env.SITE_URL || 'http://127.0.0.1:5000').replace(/\/+$/, '');
const TOKEN = process.env.WHATSAPP_BOT_TOKEN || '';
const NUMERO_BOT = (process.env.WHATSAPP_NUMERO_BOT || '5511910032144').replace(/\D/g, '');
const PASTA_SESSAO = process.env.PASTA_SESSAO || 'sessao';

const INTERVALO_CONSULTA_MS = 60 * 1000; // Pergunta ao site a cada minuto se há algo para enviar
const PAUSA_ENTRE_ENVIOS_MS = 4 * 1000; // Pausa entre mensagens para o WhatsApp não bloquear o número
const IDADE_MAXIMA_MENSAGEM_S = 120; // Ignora mensagens antigas que chegam ao reconectar

let socketAtual = null;
let conectado = false;
let enviando = false;

function carregarArquivoEnv(caminho = '.env') {
    // Lê o arquivo .env (CHAVE=valor) sem precisar de biblioteca extra
    if (!fs.existsSync(caminho)) return;
    for (const linha of fs.readFileSync(caminho, 'utf8').split(/\r?\n/)) {
        const encontrado = linha.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
        if (encontrado && process.env[encontrado[1]] === undefined) {
            process.env[encontrado[1]] = encontrado[2].replace(/^["']|["']$/g, '');
        }
    }
}

const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function chamarSite(caminho, { metodo = 'GET', corpo } = {}) {
    const resposta = await fetch(`${SITE_URL}${caminho}`, {
        method: metodo,
        headers: { 'Content-Type': 'application/json', 'X-Bot-Token': TOKEN },
        body: corpo ? JSON.stringify(corpo) : undefined,
        signal: AbortSignal.timeout(60 * 1000), // O Render gratuito pode demorar para acordar
    });
    const dados = await resposta.json().catch(() => ({}));
    if (!resposta.ok) {
        throw new Error(`Site respondeu ${resposta.status} em ${caminho}: ${dados.mensagem || ''}`);
    }
    return dados;
}

export function textoDaMensagem(msg) {
    const m = msg.message || {};
    return (
        m.conversation ||
        m.extendedTextMessage?.text ||
        m.imageMessage?.caption ||
        m.videoMessage?.caption ||
        ''
    ).trim();
}

export async function tratarMensagem(sock, msg, tipo = 'notify') {
    const jid = msg.key?.remoteJid;
    if (!jid || jid === 'status@broadcast') return;

    const enviadaEm = Number(msg.messageTimestamp || 0);
    if (enviadaEm && Date.now() / 1000 - enviadaEm > IDADE_MAXIMA_MENSAGEM_S) return;

    const texto = textoDaMensagem(msg);
    if (!texto) return;

    if (jid.endsWith('@g.us')) {
        // Nos grupos, só o dono do número (mensagem enviada pelo próprio celular do bot) dá comandos
        if (!msg.key.fromMe) return;
        const comando = texto.toLowerCase();
        if (comando === '!ativar') await ativarGrupo(sock, jid);
        else if (comando === '!desativar') await desativarGrupo(sock, jid);
        return;
    }

    // No privado, responde só a mensagens de outras pessoas que acabaram de chegar
    if (msg.key.fromMe || tipo !== 'notify') return;
    const { resposta } = await chamarSite('/api/whatsapp/mensagem', {
        metodo: 'POST',
        corpo: { numero: jid, texto },
    });
    if (resposta) await sock.sendMessage(jid, { text: resposta });
}

async function ativarGrupo(sock, jid) {
    const dados = await sock.groupMetadata(jid);
    let convite = '';
    try {
        const codigo = await sock.groupInviteCode(jid);
        if (codigo) convite = `https://chat.whatsapp.com/${codigo}`;
    } catch (erro) {
        console.warn(`Não foi possível gerar o convite do grupo ${dados.subject}: ${erro.message}`);
    }

    await chamarSite('/api/whatsapp/grupos', {
        metodo: 'POST',
        corpo: { jid, nome: dados.subject, convite },
    });
    console.log(`✅ Grupo ativado: ${dados.subject}`);

    let aviso =
        `✅ Grupo *${dados.subject}* ativado!\n\n` +
        'A partir de agora as promoções do Radar de Ofertas chegam aqui a cada 10 minutos, das 8h às 22h.';
    if (convite) aviso += `\n\nLink de convite (já está no site): ${convite}`;
    else aviso += '\n\n⚠️ Não consegui gerar o link de convite: o número do bot precisa ser administrador do grupo.';
    await sock.sendMessage(jid, { text: aviso });
}

async function desativarGrupo(sock, jid) {
    await chamarSite('/api/whatsapp/grupos/remover', { metodo: 'POST', corpo: { jid } });
    console.log(`Grupo desativado: ${jid}`);
    await sock.sendMessage(jid, { text: '⏸️ Pronto, não vou mais postar promoções neste grupo.' });
}

async function enviarOferta(sock, destino, oferta) {
    if (oferta.imagem) {
        try {
            await sock.sendMessage(destino, { image: { url: oferta.imagem }, caption: oferta.texto });
            return;
        } catch (erro) {
            console.warn(`Foto da oferta ${oferta.id} não carregou, enviando só o texto: ${erro.message}`);
        }
    }
    await sock.sendMessage(destino, { text: oferta.texto });
}

export async function enviarPendentes(sock, pausaMs = PAUSA_ENTRE_ENVIOS_MS) {
    const { envios = [] } = await chamarSite('/api/whatsapp/pendentes');
    for (const envio of envios) {
        const enviadas = [];
        for (const oferta of envio.ofertas) {
            try {
                await enviarOferta(sock, envio.destino, oferta);
                enviadas.push(oferta.id);
            } catch (erro) {
                console.error(`Erro ao enviar a oferta ${oferta.id} para ${envio.destino}: ${erro.message}`);
            }
            await esperar(pausaMs);
        }
        // Só confirma o que saiu; o restante volta no próximo ciclo
        if (enviadas.length) {
            await chamarSite('/api/whatsapp/confirmar', {
                metodo: 'POST',
                corpo: { destino: envio.destino, ofertas: enviadas },
            });
            console.log(`📤 ${enviadas.length} oferta(s) enviada(s) para ${envio.destino}`);
        }
    }
}

async function conectar() {
    const { state, saveCreds } = await useMultiFileAuthState(PASTA_SESSAO);
    const { version } = await fetchLatestBaileysVersion();
    const sock = makeWASocket({
        version,
        auth: state,
        logger: pino({ level: 'silent' }),
        browser: Browsers.macOS('Chrome'),
        markOnlineOnConnect: false, // Não aparece "online" no celular o tempo todo
    });
    socketAtual = sock;
    let pediuCodigo = false;

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
        if (qr && !sock.authState.creds.registered && !pediuCodigo) {
            pediuCodigo = true;
            const codigo = await sock.requestPairingCode(NUMERO_BOT);
            console.log('\n==============================================');
            console.log(`  Código para conectar o bot: ${codigo}`);
            console.log('==============================================');
            console.log(`No celular do número +${NUMERO_BOT}, abra o WhatsApp e vá em:`);
            console.log('Configurações > Aparelhos conectados > Conectar aparelho >');
            console.log('"Conectar com número de telefone" e digite o código acima.\n');
        }

        if (connection === 'open') {
            conectado = true;
            console.log(`✅ Bot conectado ao WhatsApp (+${NUMERO_BOT}). Consultando ${SITE_URL}`);
        }

        if (connection === 'close') {
            conectado = false;
            const codigo = lastDisconnect?.error?.output?.statusCode;
            if (codigo === DisconnectReason.loggedOut) {
                console.error(
                    `❌ O WhatsApp desconectou este aparelho. Apague a pasta "${PASTA_SESSAO}" e rode o bot de novo para gerar outro código.`
                );
                process.exit(1);
            }
            console.log('Conexão caiu, reconectando em 5 segundos...');
            setTimeout(() => conectar().catch((e) => console.error('Erro ao reconectar:', e.message)), 5000);
        }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        for (const msg of messages) {
            try {
                await tratarMensagem(sock, msg, type);
            } catch (erro) {
                console.error('Erro ao tratar mensagem recebida:', erro.message);
            }
        }
    });
}

async function cicloDeEnvio() {
    if (!conectado || enviando) return;
    enviando = true;
    try {
        await enviarPendentes(socketAtual);
    } catch (erro) {
        console.error('Erro ao buscar ofertas no site:', erro.message);
    } finally {
        enviando = false;
    }
}

async function iniciar() {
    if (!TOKEN) {
        console.error('❌ Configure WHATSAPP_BOT_TOKEN no arquivo .env (a mesma senha cadastrada no Render).');
        process.exit(1);
    }
    await conectar();
    setInterval(cicloDeEnvio, INTERVALO_CONSULTA_MS);
}

// Só inicia a conexão quando executado com "npm start" (os testes importam as funções)
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    iniciar().catch((erro) => {
        console.error('Erro ao iniciar o bot:', erro);
        process.exit(1);
    });
}
