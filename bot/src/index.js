// 팬싸 레이더 텔레그램 봇 (Cloudflare Worker)
// - 사람들이 봇에 보내는 명령(/add 아이브 등)을 받아 구독 목록을 D1(DB, schema.sql)에 저장한다. 구독 정보는 공개 저장소에 두지 않는다.
//   (처음엔 KV였는데 무료 쓰기 한도가 하루 1,000번이라 D1(하루 10만 번)으로 옮김)
// - 수집 서버(GitHub Actions)가 /subscribers 로 구독 목록을 받아 가서 사람별로 알림을 보낸다.
//   봇을 차단한 사람은 수집 서버가 /unsubscribe 로 알려 주면 지운다.
//
// 비밀 값(wrangler secret): TELEGRAM_TOKEN(봇 토큰), WEBHOOK_SECRET(텔레그램→워커 확인용), EXPORT_KEY(수집 서버 확인용),
//   ADMIN_CHAT_ID(관리자 = 테스트 알림·수집 이상 알림 받는 사람)

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
  "",
  "🌐 전체 목록·캘린더: https://fansign-radar.pages.dev",
  "🔒 저장하는 건 텔레그램 채팅 ID와 등록한 아티스트뿐이에요. /stop 하면 바로 지워져요.",
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

// ── 저장소 (D1) ──
async function getSub(env, chat) {
  const r = await env.DB.prepare("SELECT artists, all_on, overseas, joined FROM subs WHERE chat_id = ?").bind(String(chat)).first();
  if (!r) return { artists: [], all: false, overseas: false, joined: null };
  return { artists: JSON.parse(r.artists || "[]"), all: !!r.all_on, overseas: !!r.overseas, joined: r.joined };
}
const putSub = (env, chat, sub) =>
  env.DB.prepare(`INSERT INTO subs (chat_id, artists, all_on, overseas, joined, updated) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
    ON CONFLICT(chat_id) DO UPDATE SET artists = ?2, all_on = ?3, overseas = ?4, joined = COALESCE(subs.joined, ?5), updated = ?6`)
    .bind(String(chat), JSON.stringify(sub.artists || []), sub.all ? 1 : 0, sub.overseas ? 1 : 0, sub.joined || new Date().toISOString(), new Date().toISOString())
    .run();
const delSub = (env, chat) => env.DB.prepare("DELETE FROM subs WHERE chat_id = ?").bind(String(chat)).run();

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

  if (cmd === "start" && /^[A-Za-z0-9]{6,16}$/.test(arg)) {
    const row = await env.DB.prepare("SELECT artists FROM links WHERE code = ? AND expires > ?").bind(arg, Date.now()).first();
    const linked = row ? JSON.parse(row.artists) : [];
    if (linked.length) {
      const have = new Set(sub.artists.map((a) => a.toLowerCase()));
      const added = linked.filter((n) => !have.has(n.toLowerCase()));
      sub.artists = [...sub.artists, ...added].slice(0, MAX_ARTISTS);
      await putSub(env, chat, { ...sub, joined: sub.joined || new Date().toISOString() });
      await say(env, chat, HELP);
      return say(env, chat, `✅ 웹에서 고른 관심 아티스트를 등록했어요${added.length ? `: ${added.map(esc).join(", ")}` : " (이미 다 있어요)"}
지금 ${sub.artists.length}팀을 받아요. (/list)`);
    }
  }
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
    await delSub(env, chat);
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
  const { results } = await env.DB.prepare("SELECT chat_id, artists, all_on, overseas FROM subs WHERE all_on = 1 OR artists != '[]'").all();
  return results.map((r) => ({ chat_id: r.chat_id, artists: JSON.parse(r.artists || "[]"), all: !!r.all_on, overseas: !!r.overseas }));
}

const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8" } });
// 비밀 값이 비어 있으면(다른 이름으로 잘못 배포된 사본 등) 무조건 거절
const keyOk = (req, env) => !!env.EXPORT_KEY && env.EXPORT_KEY.length >= 20 && req.headers.get("authorization") === `Bearer ${env.EXPORT_KEY}`;

// ── 예비 장치: GitHub 예약 실행이 빠지면 대신 깨우기 (매시 25분, 비밀 GITHUB_TOKEN이 있을 때만) ──
const GH_REPO = "zerotoeighty/fansign-radar";
async function backupDispatch(env) {
  if (!env.GITHUB_TOKEN) return "토큰 없음";
  const h = { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "fansign-radar-bot" };
  const r = await fetch(`https://api.github.com/repos/${GH_REPO}/actions/workflows/collect.yml/runs?per_page=5`, { headers: h });
  // 토큰 만료가 2주 안이거나 이미 만료됐으면 하루 한 번(오전 9시대) 관리자에게 알림
  const exp = r.headers.get("github-authentication-token-expiration");
  const daysLeft = exp ? (Date.parse(exp.replace(" UTC", "Z").replace(" ", "T")) - Date.now()) / 86400000 : null;
  if (env.ADMIN_CHAT_ID && new Date().getUTCHours() === 0 && (r.status === 401 || (daysLeft !== null && daysLeft < 14))) {
    await say(env, env.ADMIN_CHAT_ID, r.status === 401
      ? "🔑 팬싸 레이더 예비 장치의 GitHub 토큰이 만료됐어요. 새 토큰을 만들어 Claude에게 넣어 달라고 해 주세요."
      : `🔑 팬싸 레이더 예비 장치의 GitHub 토큰이 ${Math.max(0, Math.floor(daysLeft))}일 뒤 만료돼요. 미리 새 토큰으로 바꿔 주세요.`);
  }
  if (!r.ok) return `목록 확인 실패 ${r.status}`;
  const runs = (await r.json()).workflow_runs || [];
  const recent = runs.some((x) => x.status !== "completed" || Date.now() - Date.parse(x.created_at) < 55 * 60000);
  if (recent) return "최근 실행 있음";
  const d = await fetch(`https://api.github.com/repos/${GH_REPO}/actions/workflows/collect.yml/dispatches`, {
    method: "POST", headers: { ...h, "content-type": "application/json" }, body: JSON.stringify({ ref: "main" }),
  });
  return d.status === 204 ? "대신 깨움" : `깨우기 실패 ${d.status}`;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(backupDispatch(env).then((m) => console.log("예비 장치:", m)));
  },
  async fetch(req, env) {
    const url = new URL(req.url);
    // 텔레그램 → 봇
    if (url.pathname === "/telegram" && req.method === "POST") {
      if (!env.WEBHOOK_SECRET || req.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) return new Response("forbidden", { status: 403 });
      const up = await req.json();
      try {
        if (up.message) await handleMessage(env, up.message);
        else if (up.callback_query) await handleCallback(env, up.callback_query);
      } catch (e) {
        console.log("error", e && e.stack);
      }
      return new Response("ok");
    }
    // 웹 → 관심 아티스트를 잠시 맡겨 두고 코드 받기 (t.me/봇?start=코드)
    if (url.pathname === "/link") {
      const cors = { "access-control-allow-origin": "*", "access-control-allow-methods": "POST, OPTIONS", "access-control-allow-headers": "content-type" };
      if (req.method === "OPTIONS") return new Response(null, { headers: cors });
      if (req.method !== "POST") return new Response("method", { status: 405, headers: cors });
      let artists = [];
      try { artists = (await req.json()).artists || []; } catch { /* 빈 값 */ }
      artists = [...new Set(artists.map((a) => String(a).trim().replace(/\s+/g, " ")).filter((a) => a && a.length <= 40))].slice(0, MAX_ARTISTS);
      if (!artists.length) return new Response(JSON.stringify({ error: "empty" }), { status: 400, headers: { ...cors, "content-type": "application/json" } });
      const abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
      const rnd = crypto.getRandomValues(new Uint8Array(10));
      const code = [...rnd].map((b) => abc[b % abc.length]).join("");
      await env.DB.batch([
        env.DB.prepare("DELETE FROM links WHERE expires < ?").bind(Date.now()),
        env.DB.prepare("INSERT INTO links (code, artists, expires) VALUES (?, ?, ?)").bind(code, JSON.stringify(artists), Date.now() + 30 * 86400000),
      ]);
      return new Response(JSON.stringify({ code }), { headers: { ...cors, "content-type": "application/json" } });
    }
    // 수집 서버 → 구독 목록
    if (url.pathname === "/subscribers") {
      if (!keyOk(req, env)) return new Response("forbidden", { status: 403 });
      return json({ subscribers: await allSubscribers(env), admin: env.ADMIN_CHAT_ID || null });
    }
    // 수집 서버 → 봇을 차단했거나 탈퇴한 사람 지우기
    if (url.pathname === "/unsubscribe" && req.method === "POST") {
      if (!keyOk(req, env)) return new Response("forbidden", { status: 403 });
      const ids = ((await req.json()).chat_ids || []).map(String).slice(0, 500);
      if (ids.length) await env.DB.batch(ids.map((id) => env.DB.prepare("DELETE FROM subs WHERE chat_id = ?").bind(id)));
      return json({ removed: ids.length });
    }
    // 처음 한 번: 텔레그램에 이 워커 주소 등록 + 명령 목록 설정
    if (url.pathname === "/setup" && req.method === "POST") {
      if (!keyOk(req, env)) return new Response("forbidden", { status: 403 });
      const hook = await tg(env, "setWebhook", {
        url: `${url.origin}/telegram`, secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"],
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
      await tg(env, "setMyDescription", { description:
        "📡 팬싸 레이더\n음반 판매처 18곳의 팬사인회(대면·영상통화) 새 공지를, 원하는 아티스트만 골라 바로 알려 드려요.\n🌐 fansign-radar.pages.dev\n\n시작을 누르고 /add 아이브 처럼 아티스트를 등록해 보세요." })
      await tg(env, "setMyShortDescription", { short_description: "음반 판매처 18곳의 팬싸(대면·영상통화) 새 공지를 원하는 아티스트만 골라 알려 드려요." })
      const me = await tg(env, "getMe", {});
      return json({ webhook: hook, commands: cmds.ok, bot: me.result && me.result.username });
    }
    return new Response("팬싸 레이더 봇", { status: 200 });
  },
};
