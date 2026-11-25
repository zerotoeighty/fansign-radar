"use strict";
const $ = (s, el = document) => el.querySelector(s);
const WEEK = "일월화수목금토";
const STATUS_TEXT = { ok: "", empty: "진행 팬싸 없음", blocked: "접속 막힘", error: "접속 실패", parse_error: "목록 못 읽음" };

const state = {
  data: null, events: [], config: { favorites: [], telegram: {} },
  tab: ({ "#closed": "closed", "#new": "new", "#cal": "cal" })[location.hash] || "open", format: "all",
  calMonth: null, calDay: null, favOnly: false, publicOnly: false,
  hideOverseas: localStorage.getItem("fr-hide-overseas") === "1", store: "", q: "",
};

// ───────── 유틸 ─────────
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600); }
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `오류 ${r.status}`);
  return data;
}
function kstNow() { return new Date(); }
function dayStart(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
function parseDate(s) { if (!s) return null; return s.length === 10 ? new Date(s + "T00:00:00+09:00") : new Date(s); }
function daysFromToday(d) { return Math.round((dayStart(d) - dayStart(kstNow())) / 86400000); }
function md(d) { return `${d.getMonth() + 1}/${d.getDate()}(${WEEK[d.getDay()]})`; }
function hm(d) { return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }
const norm = s => String(s || "").toLowerCase().replace(/\s+/g, "");
function favMatch(text, f) {
  f = String(f || "").trim().toLowerCase(); if (!f) return false;
  const t = String(text).toLowerCase();
  if (/^[a-z0-9 .&'!:\-]+$/.test(f)) {
    const safe = f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp("(?<![a-z0-9])" + safe + "(?![a-z0-9])");
    return re.test(t);
  }
  return t.replace(/\s+/g, "").includes(f.replace(/\s+/g, ""));
}
function isFav(ev) { return (state.config.favorites || []).some(f => favMatch(ev.artist + " " + ev.title, f)); }
const isClosed = e => e.status === "closed";
function inTab(e) {
  if (state.tab === "closed") return isClosed(e);
  if (state.tab === "new") return isNew(e);
  if (state.tab === "cal") return !state.calDay || e.event_date === state.calDay;
  return !isClosed(e);
}
function isNew(ev) { return ev.first_seen && !ev.first_seen.startsWith("base") && (Date.now() - new Date(ev.first_seen)) < 86400000; }

// ───────── 불러오기 ─────────
async function load() {
  try {
    const data = await api("/api/events");
    state.data = data; state.events = data.events || []; state.config = data.config || state.config;
    renderAll();
    if (data.running) pollUntilDone();
  } catch (e) {
    $("#list").innerHTML = `<div class="empty">서버에 연결하지 못했어요. 「팬싸 레이더 열기.bat」로 다시 켜 주세요.</div>`;
  }
}

function renderAll() {
  renderSync(); renderStats(); renderTabs(); renderStoreSelect(); renderList(); renderFavs(); renderTelegram(); renderStores();
}

function renderSync() {
  const d = state.data;
  if (!d || !d.checked_at) { $("#syncText").textContent = "아직 수집 전"; return; }
  const t = new Date(d.checked_at);
  const mins = Math.round((Date.now() - t) / 60000);
  $("#syncText").textContent = `${md(t)} ${hm(t)} 확인 · ${mins < 1 ? "방금" : mins < 60 ? mins + "분 전" : Math.floor(mins / 60) + "시간 전"}`;
}

function renderStats() {
  const evs = state.events;
  const open = evs.filter(e => !isClosed(e));
  const soon = open.filter(e => { const d = parseDate(e.apply_end); return d && e.status === "open" && daysFromToday(d) <= 1; }).length;
  $("#statLive").textContent = open.length;
  $("#statSoon").textContent = soon;
  $("#statClosed").textContent = evs.length - open.length;
  $("#statNew").textContent = evs.filter(isNew).length;
}

function renderTabs() {
  const evs = state.events;
  const open = evs.filter(e => !isClosed(e)), closed = evs.filter(isClosed), fresh = evs.filter(isNew);
  const badge = list => { const n = list.filter(isNew).length; return n ? `<span class="tabnew" data-tab="new" title="새 공지만 보기">NEW ${n}</span>` : ""; };
  const dated = evs.filter(e => e.event_date).length;
  $("#tabs").innerHTML = `
    <button type="button" data-tab="open" class="${state.tab === "open" ? "on" : ""}"><b>응모 중</b><span class="tn">${open.length}</span>${badge(open)}<small>지금 응모할 수 있거나 곧 열리는 팬싸</small></button>
    <button type="button" data-tab="closed" class="${state.tab === "closed" ? "on" : ""}"><b>응모 마감</b><span class="tn">${closed.length}</span>${badge(closed)}<small>응모는 끝났고 행사는 아직 안 열린 팬싸</small></button>
    <button type="button" data-tab="new" class="t-new ${state.tab === "new" ? "on" : ""}"><b>NEW</b><span class="tn">${fresh.length}</span><small>24시간 안에 새로 뜬 팬싸</small></button>
    <button type="button" data-tab="cal" class="t-cal ${state.tab === "cal" ? "on" : ""}"><b>캘린더</b><span class="tn">${dated}</span><small>행사 날짜별로 한눈에</small></button>`;
}

function renderStoreSelect() {
  const sel = $("#storeSel"); const cur = state.store;
  const counts = {}; state.events.filter(inTab).forEach(e => counts[e.store] = (counts[e.store] || 0) + 1);
  const names = (state.data?.sources || []).map(s => s.store);
  sel.innerHTML = `<option value="">모든 판매처</option>` + names.map(n => `<option value="${esc(n)}"${n === cur ? " selected" : ""}>${esc(n)} ${counts[n] || 0}</option>`).join("");
}

function filtered() {
  const q = norm(state.q);
  return state.events.filter(e =>
    inTab(e) &&
    (state.format === "all" || (e.formats || []).includes(state.format)) &&
    (!state.favOnly || isFav(e)) &&
    (!state.publicOnly || e.public) &&
    (!state.hideOverseas || !e.overseas) &&
    (!state.store || e.store === state.store) &&
    (!q || norm(`${e.artist} ${e.title} ${e.store}`).includes(q)));
}

function dueHtml(e) {
  const end = parseDate(e.apply_end);
  const src = e.schedule_src === "image" ? `<span class="sub ocr" title="공지 이미지에서 글자 인식으로 읽은 일정이에요. 원문으로 한 번 확인해 주세요.">이미지에서 읽음</span>`
    : e.schedule_src === "x" ? `<a class="sub ocr xsrc" href="${esc(e.schedule_url || e.url)}" target="_blank" rel="noopener" title="판매처 X(트위터) 공지에서 읽은 일정이에요.">X 공지에서 읽음 ↗</a>` : "";
  const win = parseDate(e.winner);
  if (e.status === "closed") {
    const sub = win ? `발표 ${md(win)}${win.getHours() || win.getMinutes() ? " " + hm(win) : ""}` : e.winner_posted ? "당첨자 발표됨" : end ? `~${md(end)} 마감됨` : "";
    return `<span class="due closed"><span class="lab">응모 기간</span>응모 마감${sub ? `<span class="sub">${sub}</span>` : ""}${src}</span>`;
  }
  if (!end) return `<span class="due unk"><span class="lab">응모 기간</span>확인 필요<span class="sub">원문에서 확인해 주세요</span></span>`;
  const n = daysFromToday(end);
  const time = (end.getHours() === 23 && end.getMinutes() === 59) ? "" : " " + hm(end);
  const start = parseDate(e.apply_start);
  if (start && start > kstNow()) return `<span class="due"><span class="lab">응모 기간</span>${md(start)}${start.getHours() ? " " + hm(start) : ""} 오픈<span class="sub">~${md(end)}${time}</span>${src}</span>`;
  if (n <= 0) return `<span class="due hot"><span class="lab">응모 기간</span>오늘${time} 마감<span class="sub">서둘러 주세요</span>${src}</span>`;
  if (n === 1) return `<span class="due hot"><span class="lab">응모 기간</span>내일 마감<span class="sub">~${md(end)}${time}</span>${src}</span>`;
  return `<span class="due"><span class="lab">응모 기간</span>D-${n} 마감<span class="sub">~${md(end)}${time}</span>${src}</span>`;
}

function mergeCards(list) {
  // 같은 판매처·같은 아티스트·같은 행사일(대면/1:1 영통/단체 영통 등)은 한 장으로
  const out = []; const idx = new Map();
  for (const e of list) {
    const k = `${e.store}|${norm(e.artist)}|${e.event_date || e.id}|${e.status}`;
    if (idx.has(k)) {
      const m = idx.get(k);
      m.items.push(e);
      m.formats = [...new Set([...m.formats, ...(e.formats || [])])].sort();
      m.public = m.public || e.public; m.venue = m.venue || e.venue; m.overseas = m.overseas || e.overseas;
      if (e.apply_end && (!m.apply_end || e.apply_end < m.apply_end)) { m.apply_end = e.apply_end; m.apply_start = e.apply_start; }
      m.winner = m.winner || e.winner; m.last_fansign = m.last_fansign || e.last_fansign; m.schedule_src = m.schedule_src || e.schedule_src; m.first_seen = isNew(e) ? e.first_seen : m.first_seen;
    } else {
      const m = { ...e, formats: [...(e.formats || [])], items: [e] };
      idx.set(k, m); out.push(m);
    }
  }
  return out;
}

function subLabel(e) {
  const t = e.title;
  const m = t.match(/(단체|스페셜|릴레이|relay|special)?\s*(1:1)?\s*(영상\s*통화|영통|video\s*call|대면|오프라인|offline|meet\s*&\s*call|fan\s*sign)/i);
  return m ? m[0].trim() : (e.formats || []).join("/");
}

function cardHtml(e) {
  const fav = isFav(e);
  const tags = [];
  if (isNew(e)) tags.push(`<span class="tag new" data-tab="new" title="새로 뜬 팬싸만 보기">NEW</span>`);
  if (e.last_fansign) tags.push(`<span class="tag last">마지막 팬싸</span>`);
  (e.formats || []).forEach(f => tags.push(`<span class="tag ${f === "대면" ? "meet" : "call"}">${f}</span>`));
  if (e.public) tags.push(`<span class="tag pub">공개 팬싸${e.venue ? " · " + esc(e.venue) : ""}</span>`);
  if (e.overseas) tags.push(`<span class="tag ov">해외</span>`);
  const win = parseDate(e.winner);
  if (win && e.status !== "closed") tags.push(`<span class="tag win">발표 ${md(win)}</span>`);
  tags.push(`<span class="tag store">${esc(e.store)}</span>`);
  const many = e.items && e.items.length > 1;
  const body = many
    ? `<ul class="subs">${e.items.map(x => `<li><span class="k">${esc(subLabel(x))}</span><a href="${esc(x.url)}" target="_blank" rel="noopener" title="${esc(x.title)}">${esc(x.title)}</a></li>`).join("")}</ul>`
    : `<p class="title">${esc(e.title)}</p>`;
  return `<article class="card${e.status === "closed" ? " is-closed" : ""}${e.public ? " is-public" : ""}">
    <div class="who"><button type="button" class="star${fav ? " on" : ""}" data-artist="${esc(e.artist)}" aria-label="${esc(e.artist)} 관심 아티스트">★</button><span class="artist">${esc(e.artist)}</span></div>
    ${body}
    <div class="tags">${tags.join("")}</div>
    <div class="right">${dueHtml(e)}<a class="open" href="${esc(e.url)}" target="_blank" rel="noopener">${many ? `원문 확인 ${e.items.length}개 ↗` : "원문 확인 ↗"}</a></div>
  </article>`;
}

const TAB_NAME = { open: "응모 중", closed: "응모 마감", new: "NEW · 새로 뜬 팬싸", cal: "캘린더" };

function renderList() {
  renderCalendar();
  if (state.tab === "cal" && !state.calDay) { $("#countLine").textContent = "날짜를 누르면 그날 팬싸를 아래에 보여 줘요."; $("#list").innerHTML = ""; return; }
  const raw = filtered();
  const list = mergeCards(raw);
  $("#countLine").textContent = state.tab === "cal"
    ? `${parseDate(state.calDay).getMonth() + 1}월 ${parseDate(state.calDay).getDate()}일 · 공지 ${raw.length}건 · 카드 ${list.length}장`
    : `${TAB_NAME[state.tab]} · 공지 ${raw.length}건 · 카드 ${list.length}장 · 행사 날짜순`;
  if (!list.length) {
    $("#list").innerHTML = `<div class="empty">${state.events.length ? (state.tab === "closed" ? "응모 마감된 팬싸 중 조건에 맞는 게 없어요." : state.tab === "new" ? "24시간 안에 새로 뜬 팬싸가 아직 없어요." : state.tab === "cal" ? "이 날은 조건에 맞는 팬싸가 없어요." : "조건에 맞는 응모 중 팬싸가 없어요. 필터를 바꿔 보세요.") : "지금 확인된 팬싸 공지가 없어요."}</div>`;
    return;
  }
  const groups = new Map();
  for (const e of list) {
    const k = e.event_date || "none";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(e);
  }
  const keys = [...groups.keys()].sort((a, b) => a === "none" ? 1 : b === "none" ? -1 : a.localeCompare(b));
  $("#list").innerHTML = keys.map(k => {
    let head;
    if (k === "none") head = `<div class="gh"><span class="d">날짜 미정</span><span class="w">행사일은 원문 확인</span></div>`;
    else {
      const d = parseDate(k); const n = daysFromToday(d);
      const dd = n === 0 ? "오늘" : n === 1 ? "내일" : `D-${n}`;
      head = `<div class="gh${n <= 1 ? " today" : ""}"><span class="d">${d.getMonth() + 1}.${String(d.getDate()).padStart(2, "0")}</span><span class="w">${WEEK[d.getDay()]}요일</span><span class="dd">${dd} · ${groups.get(k).length}건</span></div>`;
    }
    return `<section class="group">${head}${groups.get(k).map(cardHtml).join("")}</section>`;
  }).join("");
}

// ───────── 캘린더 ─────────
function ymd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function calEvents() {
  // 캘린더는 응모 중·마감 모두 (다른 필터는 그대로 적용)
  const keep = state.tab; state.tab = "_all";
  const q = norm(state.q);
  const list = state.events.filter(e => e.event_date &&
    (state.format === "all" || (e.formats || []).includes(state.format)) &&
    (!state.favOnly || isFav(e)) && (!state.publicOnly || e.public) &&
    (!state.hideOverseas || !e.overseas) && (!state.store || e.store === state.store) &&
    (!q || norm(`${e.artist} ${e.title} ${e.store}`).includes(q)));
  state.tab = keep;
  return list;
}
function renderCalendar() {
  const box = $("#calendar");
  if (state.tab !== "cal") { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  if (!state.calMonth) { const n = kstNow(); state.calMonth = new Date(n.getFullYear(), n.getMonth(), 1); }
  const m = state.calMonth, y = m.getFullYear(), mo = m.getMonth();
  const byDay = new Map();
  for (const e of mergeCards(calEvents())) {
    if (!byDay.has(e.event_date)) byDay.set(e.event_date, []);
    byDay.get(e.event_date).push(e);
  }
  const first = new Date(y, mo, 1), start = new Date(y, mo, 1 - first.getDay());
  const today = ymd(kstNow());
  let cells = "";
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const rank = e => isClosed(e) ? 2 : e.status === "unknown" ? 1 : 0;   // 칸에는 응모 중부터
    const k = ymd(d), list = (byDay.get(k) || []).slice().sort((a, b) => rank(a) - rank(b) || (isFav(b) - isFav(a)));
    if (i >= 35 && d.getMonth() !== mo) break;
    const chips = list.slice(0, 4).map(e => {
      const cls = isClosed(e) ? "c-closed" : e.status === "unknown" ? "c-unk" : "c-open";
      return `<span class="cchip ${cls}${isNew(e) ? " c-new" : ""}${isFav(e) ? " c-fav" : ""}${e.public ? " c-pub" : ""}" title="${esc(e.artist)} · ${esc(e.store)} · ${isClosed(e) ? "응모 마감 (행사는 예정대로)" : e.status === "unknown" ? "응모 기간 확인 필요" : "응모 중"}">${esc(e.artist.replace(/\s+\([^()]*\)$/, ""))}</span>`;
    }).join("");
    const more = list.length > 4 ? `<span class="cmore">+${list.length - 4}</span>` : "";
    cells += `<button type="button" class="cday${d.getMonth() !== mo ? " out" : ""}${k === today ? " today" : ""}${k === state.calDay ? " sel" : ""}${list.length ? " has" : ""}" data-day="${k}">
      <span class="cnum">${d.getDate()}${list.length ? `<i>${list.length}</i>` : ""}</span>${chips}${more}</button>`;
  }
  box.innerHTML = `<div class="calhead">
      <button type="button" class="ghost" data-cal="-1" aria-label="이전 달">‹</button>
      <strong>${y}년 ${mo + 1}월</strong>
      <button type="button" class="ghost" data-cal="1" aria-label="다음 달">›</button>
      <span class="legend"><span class="cchip c-open">응모 중</span><span class="cchip c-unk">기간 확인 필요</span><span class="cchip c-closed">응모 마감 · 행사 예정</span><span class="cchip c-unk c-new">NEW</span><span class="cchip c-pubs">공개 팬싸</span></span>
    </div>
    <div class="cgrid">${[..."일월화수목금토"].map(w => `<span class="cw">${w}</span>`).join("")}${cells}</div>`;
}

// ───────── 관심 아티스트 ─────────
function renderFavs() {
  const favs = state.config.favorites || [];
  $("#favList").innerHTML = favs.length
    ? favs.map(f => `<span class="chip">${esc(f)}<button type="button" data-remove="${esc(f)}" aria-label="${esc(f)} 빼기">×</button></span>`).join("")
    : `<span class="hint">아직 없어요.</span>`;
  $("#favOnly").classList.toggle("on", state.favOnly);
}
async function saveFavs(favs) {
  const cfg = await api("/api/favorites", { favorites: favs });
  state.config = cfg; renderFavs(); renderList(); renderTelegram();
}

// ───────── 텔레그램 ─────────
function notifyScopeHtml() {
  const favN = (state.config.favorites || []).length;
  const favOnly = state.config.notify !== "all";
  return `<div class="scope">
      <p class="scope-h">알림 받을 공지</p>
      <label><input type="radio" name="notify" value="favorites" ${favOnly ? "checked" : ""}> <b>관심 아티스트만</b> ${favN ? `<span class="hint" style="margin:0">(${favN}명)</span>` : ""}</label>
      <label><input type="radio" name="notify" value="all" ${!favOnly ? "checked" : ""}> 새 팬싸 전부</label>
      <label><input type="checkbox" id="tgOverseas" ${state.config.include_overseas_alert ? "checked" : ""}> 해외 행사도 받기</label>
      ${favOnly && !favN ? `<p class="warn">관심 아티스트가 비어 있어서 지금은 알림이 오지 않아요. 위에서 아티스트를 추가해 주세요.</p>` : ""}
      <p class="hint" style="margin:6px 0 0">새로 올라온 응모 중 공지만 보내요. 마감된 공지는 보내지 않아요.</p>
    </div>`;
}

function renderTelegram() {
  const tg = state.config.telegram || {};
  const body = $("#tgBody");
  if (!tg.has_token) {
    body.innerHTML = `<p class="hint">새 공지가 올라오면 휴대폰으로 알려 줘요. 처음 한 번만 설정하면 돼요.</p>
      <ol class="steps">
        <li>텔레그램에서 <b>@BotFather</b>를 찾아 <code>/newbot</code>을 보내요. 봇 이름과 아이디(끝이 <code>bot</code>)를 정하면 <b>토큰</b>을 줘요.</li>
        <li>받은 토큰을 아래에 붙여 넣어요.
          <form id="tgTokenForm" class="addrow" style="margin-top:6px"><input id="tgToken" placeholder="1234567890:AA…" autocomplete="off"><button type="submit">연결</button></form>
        </li>
      </ol>
      <p class="hint">토큰은 이 PC의 config.json에만 저장돼요.</p>${notifyScopeHtml()}`;
    return;
  }
  if (!tg.connected) {
    body.innerHTML = `<p class="tgstate">봇 <b>@${esc(tg.bot)}</b> 확인됨</p>
      <ol class="steps" start="3">
        <li><a href="https://t.me/${esc(tg.bot)}" target="_blank" rel="noopener">t.me/${esc(tg.bot)}</a>을 열고 <b>시작(/start)</b>을 눌러요.</li>
        <li>그다음 아래 버튼을 눌러요.</li>
      </ol>
      <div class="tgrow"><button class="btn" id="tgFind" type="button">채팅 찾기</button><button class="btn sub" id="tgReset" type="button">처음부터</button></div>${notifyScopeHtml()}`;
    return;
  }
  body.innerHTML = `<p class="tgstate"><b>● 연결됨</b> · ${esc(tg.chat)} (@${esc(tg.bot)})</p>
    <label class="radio" style="flex-direction:row"><input type="checkbox" id="tgEnabled" ${tg.enabled ? "checked" : ""}> 알림 켜기</label>
    ${notifyScopeHtml()}
    <div class="tgrow"><button class="btn" id="tgTest" type="button">테스트 알림</button><button class="btn sub" id="tgReset" type="button">연결 해제</button></div>
    <p class="hint" style="margin-top:10px">1시간마다 수집해요.</p>`;
}

// ───────── 판매처 상태 ─────────
function renderStores() {
  const src = state.data?.sources || [];
  $("#storeList").innerHTML = src.map(s => {
    const label = s.status === "ok" ? `${s.count}건` : s.carried ? `${STATUS_TEXT[s.status]} · 이전 ${s.carried}건 유지` : STATUS_TEXT[s.status] || s.status;
    const bad = ["blocked", "error", "parse_error"].includes(s.status);
    return `<li title="${esc(s.message || "")}"><span class="dot st-${s.status}"></span><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.store)}</a><span class="n${bad ? " bad" : ""}">${esc(label)}</span></li>`;
  }).join("");
  const d = state.data;
  $("#tookLine").textContent = d?.took_seconds ? `마지막 수집 ${d.took_seconds}초 · 1시간마다 자동` : "";
}

// ───────── 새로고침 ─────────
async function pollUntilDone() {
  const btn = $("#refreshBtn");
  btn.disabled = true; btn.textContent = "수집 중…";
  for (let i = 0; i < 90; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const s = await api("/api/status").catch(() => ({ running: false }));
    if (!s.running) break;
  }
  btn.disabled = false; btn.textContent = "지금 새로고침";
  const before = state.events.length;
  await load();
  toast(`수집 완료 · 팬싸 ${state.events.length}건${state.events.length !== before ? ` (이전 ${before}건)` : ""}`);
}

// ───────── 이벤트 연결 ─────────
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-tab], [data-day], [data-cal], button, [data-format]");
  if (!t) return;
  try {
    if (t.dataset.tab) {
      state.tab = t.dataset.tab; if (state.tab !== "cal") state.calDay = null;
      history.replaceState(null, "", state.tab === "open" ? location.pathname : "#" + state.tab);
      renderTabs(); renderStoreSelect(); renderList();
    } else if (t.dataset.day) {
      state.calDay = state.calDay === t.dataset.day ? null : t.dataset.day; renderList();
      if (state.calDay) $("#countLine").scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (t.dataset.cal) {
      state.calMonth = new Date(state.calMonth.getFullYear(), state.calMonth.getMonth() + Number(t.dataset.cal), 1);
      state.calDay = null; renderList();
    } else if (t.dataset.format) {
      state.format = t.dataset.format;
      document.querySelectorAll(".seg button").forEach(b => b.classList.toggle("on", b === t));
      renderList();
    } else if (t.classList.contains("star")) {
      const a = t.dataset.artist; const favs = [...(state.config.favorites || [])];
      const hit = favs.findIndex(f => norm(a).includes(norm(f)) || norm(f) === norm(a));
      if (hit >= 0) { favs.splice(hit, 1); toast(`${a} 관심 해제`); } else { favs.push(a); toast(`${a} 관심 아티스트에 추가`); }
      await saveFavs(favs);
    } else if (t.dataset.remove) {
      await saveFavs((state.config.favorites || []).filter(f => f !== t.dataset.remove));
    } else if (t.id === "favOnly") { state.favOnly = !state.favOnly; t.classList.toggle("on", state.favOnly); renderList(); }
    else if (t.id === "publicOnly") { state.publicOnly = !state.publicOnly; t.classList.toggle("on", state.publicOnly); renderList(); }
    else if (t.id === "hideOverseas") { state.hideOverseas = !state.hideOverseas; localStorage.setItem("fr-hide-overseas", state.hideOverseas ? "1" : "0"); t.classList.toggle("on", state.hideOverseas); renderList(); }
    else if (t.id === "refreshBtn") { await api("/api/collect", {}); pollUntilDone(); }
    else if (t.id === "tgFind") { t.disabled = true; state.config = await api("/api/telegram/find", {}); renderTelegram(); toast("연결됐어요! 텔레그램을 확인해 보세요."); }
    else if (t.id === "tgTest") { t.disabled = true; await api("/api/telegram/test", {}); toast("테스트 알림을 보냈어요."); t.disabled = false; }
    else if (t.id === "tgReset") { state.config = await api("/api/telegram/reset", {}); renderTelegram(); }
  } catch (e) { toast(e.message); t.disabled = false; }
});

document.addEventListener("change", async (ev) => {
  const t = ev.target;
  try {
    if (t.id === "storeSel") { state.store = t.value; renderList(); }
    else if (t.name === "notify") { state.config = await api("/api/settings", { notify: t.value }); renderTelegram(); }
    else if (t.id === "tgOverseas") { state.config = await api("/api/settings", { include_overseas_alert: t.checked }); }
    else if (t.id === "tgEnabled") { state.config = await api("/api/settings", { enabled: t.checked }); toast(t.checked ? "알림을 켰어요." : "알림을 껐어요."); }
  } catch (e) { toast(e.message); }
});

document.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    if (f.id === "favForm") {
      const v = $("#favInput").value.split(",").map(s => s.trim()).filter(Boolean);
      if (!v.length) return;
      await saveFavs([...(state.config.favorites || []), ...v]);
      $("#favInput").value = "";
    } else if (f.id === "tgTokenForm") {
      const token = $("#tgToken").value.trim();
      if (!token) return;
      state.config = await api("/api/telegram/token", { token });
      renderTelegram(); toast("봇을 확인했어요.");
    }
  } catch (e) { toast(e.message.includes("Unauthorized") ? "토큰이 맞지 않아요. 다시 확인해 주세요." : e.message); }
});

$("#q").addEventListener("input", e => { state.q = e.target.value; renderList(); });
$("#hideOverseas").classList.toggle("on", state.hideOverseas);
setInterval(renderSync, 60000);
setInterval(() => { if (!$("#refreshBtn").disabled) load(); }, 10 * 60000);
load();
