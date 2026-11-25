// 팬싸 레이더 텔레그램 봇 (Cloudflare Worker)
// - 사람들이 봇에 보내는 명령(/add 아이브 등)을 받아 구독 목록을 KV(SUBS)에 저장한다. 구독 정보는 공개 저장소에 두지 않는다.
// - 수집 서버(GitHub Actions)가 /subscribers 로 구독 목록을 받아 가서 사람별로 알림을 보낸다.
//
// 비밀 값(wrangler secret): TELEGRAM_TOKEN(봇 토큰), WEBHOOK_SECRET(텔레그램→워커 확인용), EXPORT_KEY(수집 서버 확인용)

const EVENTS_URL = "https://raw.githubusercontent.com/zerotoeighty/fansign-radar/data/events.json";
const MAX_ARTISTS = 50;

const HELP = [
  "📡 <b>팬싸 레이더</b>",
  "음반 판매처 18곳의 팬사인회(대면·영상통화) 새 공지를 알려 드려요.",
  "",
  "<b>쓰는 법</b>",
  "/add 아이브 — 아티스트 추가 (쉼표로 여러 명: <code>/add 아이브, NMIXX</code>)",
  "/del 아이브 — 빼기",
  "/list — 내 목록 (버튼으로 빼기)",
  "/all — 아티스트 상관없이 모든 새 공지 받기 (켜기/끄기)",
  "/overseas — 해외 팬싸도 받기 (켜기/끄기)",
  "/now — 지금 응모 중인 아티스트 보기",
  "/stop — 알림 그만 받기 (목록 삭제)",
  "",
  "영문 이름은 단어 단위로 맞춰요 (IVE ≠ DRIVE). 한글은 띄어쓰기 상관없어요.",
  "새 공지는 매시 확인해요.",
].join("\n");

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

const say = (env, chat, text, extra = {}) =>
  tg(env, "sendMessage", { chat_id: chat, text, parse_mode: "HTML", disable_web_page_preview: true, ...extra });

async function getSub(env, chat) {
  const v = await env.SUBS.get(`u:${chat}`, "json");
  return v || { artists: [], all: false, overseas: false };
}
const putSub = (env, chat, sub) => env.SUBS.put(`u:${chat}`, JSON.stringify({ ...sub, updated: new Date().toISOString() }));

function listMessage(sub) {
  const lines = ["📋 <b>내 알림 목록</b>"];
  if (sub.all) lines.push("• 모든 새 공지 받기: <b>켜짐</b>");
  lines.push(sub.artists.length ? sub.artists.map((a) => `• ${esc(a)}`).join("\n") : "(아티스트 없음 — /add 아이브 처럼 추가해 주세요)");
  lines.push(`• 해외 팬싸: ${sub.overseas ? "받음" : "안 받음"}`);
  const keyboard = sub.artists.map((a, i) => [{ text: `✕ ${a}`, callback_data: `del:${i}` }]);
  return { text: lines.join("\n"), extra: keyboard.length ? { reply_markup: { inline_keyboard: keyboard } } : {} };
}

const splitNames = (s) =>
  s.split(/[,，\n]/).map((x) => x.trim().replace(/\s+/g, " ")).filter((x) => x && x.length <= 40);

async function nowOpen(env) {
  try {
    const r = await fetch(EVENTS_URL, { cf: { cacheTtl: 300 } });
    const data = await r.json();
    const names = new Map();
    for (const e of data.events || []) {
      if (e.status === "open" || e.status === "unknown") names.set(e.artist, (names.get(e.artist) || 0) + 1);
    }
    if (!names.size) return "지금 응모 중인 팬싸가 없어요.";
    const list = [...names.keys()].sort((a, b) => a.localeCompare(b, "ko"));
    return `🟢 <b>지금 응모 중</b> (${list.length}팀)\n` + list.map(esc).join(", ") + "\n\n이름 그대로 /add 하시면 돼요.";
  } catch {
    return "목록을 불러오지 못했어요. 잠시 뒤에 다시 해 주세요.";
  }
}

async function handleMessage(env, msg) {
  const chat = msg.chat.id;
  if (msg.chat.type !== "private") return; // 개인 대화에서만
  const text = (msg.text || "").trim();
  const m = text.match(/^\/(\w+)(?:@\w+)?\s*([\s\S]*)$/);
  const cmd = m ? m[1].toLowerCase() : "";
  const arg = m ? m[2].trim() : text;
  const sub = await getSub(env, chat);

  if (cmd === "start" || cmd === "help") {
    if (!sub.joined) await putSub(env, chat, { ...sub, joined: new Date().toISOString() });
    return say(env, chat, HELP);
  }
  if (!cmd) return say(env, chat, "아티스트를 추가하려면 앞에 /add 를 붙여 주세요. 예: <code>/add 아이브</code>\n사용법은 /help");
  if (cmd === "add") {
    const names = splitNames(arg);
    if (!names.length) return say(env, chat, "아티스트 이름을 같이 보내 주세요. 예: <code>/add 아이브</code>");
    const have = new Set(sub.artists.map((a) => a.toLowerCase()));
    const added = names.filter((n) => !have.has(n.toLowerCase()));
    sub.artists = [...sub.artists, ...added].slice(0, MAX_ARTISTS);
    await putSub(env, chat, { ...sub, joined: sub.joined || new Date().toISOString() });
    return say(env, chat, added.length ? `✅ 추가했어요: ${added.map(esc).join(", ")}\n지금 ${sub.artists.length}팀을 받아요. (/list)` : "이미 목록에 있어요. (/list)");
  }
  if (cmd === "del") {
    const names = splitNames(arg).map((n) => n.toLowerCase());
    const before = sub.artists.length;
    sub.artists = sub.artists.filter((a) => !names.includes(a.toLowerCase()));
    await putSub(env, chat, sub);
    return say(env, chat, before !== sub.artists.length ? "🗑 뺐어요. (/list)" : "목록에 없는 이름이에요. /list 에서 버튼으로 빼도 돼요.");
  }
  if (cmd === "list") {
    const { text: t, extra } = listMessage(sub);
    return say(env, chat, t, extra);
  }
  if (cmd === "all") {
    sub.all = !sub.all;
    await putSub(env, chat, sub);
    return say(env, chat, sub.all ? "🔔 모든 새 공지를 받아요. (다시 /all 하면 꺼져요)" : "🔕 이제 목록의 아티스트만 받아요.");
  }
  if (cmd === "overseas") {
    sub.overseas = !sub.overseas;
    await putSub(env, chat, sub);
    return say(env, chat, sub.overseas ? "🌏 해외 팬싸도 받아요." : "해외 팬싸는 받지 않아요.");
  }
  if (cmd === "now") return say(env, chat, await nowOpen(env));
  if (cmd === "stop") {
    await env.SUBS.delete(`u:${chat}`);
    return say(env, chat, "알림을 멈추고 목록을 지웠어요. 다시 받으려면 /start");
  }
  return say(env, chat, "모르는 명령이에요.\n\n" + HELP);
}

async function handleCallback(env, cq) {
  const chat = cq.message.chat.id;
  const sub = await getSub(env, chat);
  const m = /^del:(\d+)$/.exec(cq.data || "");
  if (m && sub.artists[+m[1]] !== undefined) {
    const gone = sub.artists.splice(+m[1], 1)[0];
    await putSub(env, chat, sub);
    const { text, extra } = listMessage(sub);
    await tg(env, "editMessageText", { chat_id: chat, message_id: cq.message.message_id, text, parse_mode: "HTML", ...extra });
    return tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: `${gone} 뺐어요` });
  }
  return tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
}

async function allSubscribers(env) {
  const out = [];
  let cursor;
  do {
    const page = await env.SUBS.list({ prefix: "u:", cursor });
    for (const k of page.keys) {
      const v = await env.SUBS.get(k.name, "json");
      if (v && (v.all || (v.artists || []).length)) out.push({ chat_id: k.name.slice(2), artists: v.artists || [], all: !!v.all, overseas: !!v.overseas });
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8" } });
const keyOk = (req, env) => req.headers.get("authorization") === `Bearer ${env.EXPORT_KEY}`;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    // 텔레그램 → 봇
    if (url.pathname === "/telegram" && req.method === "POST") {
      if (req.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) return new Response("forbidden", { status: 403 });
      const up = await req.json();
      try {
        if (up.message) await handleMessage(env, up.message);
        else if (up.callback_query) await handleCallback(env, up.callback_query);
      } catch (e) {
        console.log("error", e && e.stack);
      }
      return new Response("ok");
    }
    // 수집 서버 → 구독 목록
    if (url.pathname === "/subscribers") {
      if (!keyOk(req, env)) return new Response("forbidden", { status: 403 });
      return json({ subscribers: await allSubscribers(env) });
    }
    // 처음 한 번: 텔레그램에 이 워커 주소 등록 + 명령 목록 설정
    if (url.pathname === "/setup" && req.method === "POST") {
      if (!keyOk(req, env)) return new Response("forbidden", { status: 403 });
      const hook = await tg(env, "setWebhook", {
        url: `${url.origin}/telegram`, secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"], drop_pending_updates: true,
      });
      const cmds = await tg(env, "setMyCommands", {
        commands: [
          { command: "add", description: "아티스트 추가 (예: /add 아이브)" },
          { command: "list", description: "내 알림 목록" },
          { command: "del", description: "아티스트 빼기" },
          { command: "now", description: "지금 응모 중인 아티스트" },
          { command: "all", description: "모든 새 공지 받기 켜기/끄기" },
          { command: "overseas", description: "해외 팬싸 받기 켜기/끄기" },
          { command: "help", description: "사용법" },
          { command: "stop", description: "알림 그만 받기" },
        ],
      });
      const me = await tg(env, "getMe", {});
      return json({ webhook: hook, commands: cmds.ok, bot: me.result && me.result.username });
    }
    return new Response("팬싸 레이더 봇", { status: 200 });
  },
};
