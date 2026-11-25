"use strict";
const $ = (s, el = document) => el.querySelector(s);
const WEEK = "일월화수목금토";
const STATUS_TEXT = { ok: "", empty: "진행 팬싸 없음", blocked: "접속 막힘", error: "접속 실패", parse_error: "목록 못 읽음", pc: "" };
// 이 PC(127.0.0.1:8771)에서 열면 PC 서버(api), 웹(pages.dev)에서 열면 GitHub에 저장된 서버 결과를 바로 읽는다
const LOCAL = /^(127\.0\.0\.1|localhost)$/.test(location.hostname);
const EVENTS_URL = "https://raw.githubusercontent.com/zerotoeighty/fansign-radar/data/events.json";
const BOT = "fansign_radar_bot";
const BOT_API = "https://fansign-radar-bot.fansign-radar.workers.dev";
const FAV_KEY = "fr-favorites";

const state = {
  data: null, events: [], config: { favorites: [], telegram: {} },
  tab: ({ "#today": "today", "#closed": "closed", "#new": "new", "#cal": "cal" })[location.hash] || "open", format: "all",
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
// 오늘(한국 시간) 응모 마감 + 마감 시각이 아직 안 지남
function isToday(e) { const d = parseDate(e.apply_end); return !isClosed(e) && !!d && d > Date.now() && daysFromToday(d) === 0; }
function inTab(e) {
  if (state.tab === "today") return isToday(e);
  if (state.tab === "closed") return isClosed(e);
  if (state.tab === "new") return isNew(e);
  if (state.tab === "cal") return !state.calDay || e.event_date === state.calDay;
  return !isClosed(e);
}
function isNew(ev) { return ev.first_seen && !ev.first_seen.startsWith("base") && (Date.now() - new Date(ev.first_seen)) < 86400000; }

// ───────── 불러오기 ─────────
function localFavs() { try { return JSON.parse(localStorage.getItem(FAV_KEY) || "[]"); } catch { return []; } }
async function load() {
  try {
    if (LOCAL) {
      const data = await api("/api/events");
      state.data = data; state.events = data.events || []; state.config = data.config || state.config;
      renderAll();
      if (data.running) pollUntilDone();
    } else {
      const r = await fetch(EVENTS_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      state.data = data; state.events = data.events || []; state.config = { favorites: localFavs() };
      renderAll();
    }
  } catch (e) {
    $("#list").innerHTML = LOCAL
      ? `<div class="empty">서버에 연결하지 못했어요. 「팬싸 레이더 열기.bat」로 다시 켜 주세요.</div>`
      : `<div class="empty">팬싸 목록을 불러오지 못했어요. 잠시 뒤에 새로고침해 주세요.</div>`;
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
  const open = evs.filter(e => !isClosed(e)), closed = evs.filter(isClosed), fresh = evs.filter(isNew), today = evs.filter(isToday);
  const badge = list => { const n = list.filter(isNew).length; return n ? `<span class="tabnew" data-tab="new" title="새 공지만 보기">NEW ${n}</span>` : ""; };
  const dated = evs.filter(e => e.event_date).length;
  $("#tabs").innerHTML = `
    <button type="button" data-tab="open" class="${state.tab === "open" ? "on" : ""}"><b>응모 중</b><span class="tn">${open.length}</span>${badge(open)}<small>지금 응모할 수 있거나 곧 열리는 팬싸</small></button>
    <button type="button" data-tab="today" class="t-today ${state.tab === "today" ? "on" : ""}"><b>오늘 마감</b><span class="tn">${today.length}</span><small>오늘 응모가 끝나는 팬싸, 마감 시각순</small></button>
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
  if (n <= 0) return `<span class="due hot"><span class="lab">응모 기간</span>오늘 ${hm(end)} 마감<span class="sub">서둘러 주세요</span>${src}</span>`;
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

// ───────── 캘린더에 추가 (행사일 하루 일정) ─────────
const CAL_CARDS = [];
function calInfo(e) {
  const d = e.event_date.replace(/-/g, "");
  const next = parseDate(e.event_date); next.setDate(next.getDate() + 1);
  const end = ymd(next).replace(/-/g, "");
  const fm = (e.formats || []).join("·");
  const title = `${e.artist} 팬사인회${fm ? ` (${fm})` : ""} · ${e.store}`;
  const due = parseDate(e.apply_end);
  const details = [e.title, due ? `응모 마감 ${md(due)} ${hm(due)}` : "", e.url, "", "팬싸 레이더 https://fansign-radar.pages.dev"].filter((x, i) => x || i === 3).join("\n");
  return { d, end, title, details };
}
function calHtml(e) {
  if (!e.event_date) return "";
  const i = CAL_CARDS.push(e) - 1;
  const c = calInfo(e);
  const g = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(c.title)}&dates=${c.d}/${c.end}&details=${encodeURIComponent(c.details)}`;
  return `<details class="addcal"><summary>캘린더에 추가</summary><div class="calmenu"><a href="${esc(g)}" target="_blank" rel="noopener">구글 캘린더</a><a href="#" data-ics="${i}">아이폰·기타 (.ics)</a></div></details>`;
}
function downloadIcs(e) {
  const c = calInfo(e);
  const icsEsc = s => String(s).replace(/[\\;,]/g, m => "\\" + m).replace(/\n/g, "\\n");
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//fansign-radar//KO", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
    `UID:${e.id || c.d + Math.random().toString(36).slice(2)}@fansign-radar`, `DTSTAMP:${stamp}`,
    `DTSTART;VALUE=DATE:${c.d}`, `DTEND;VALUE=DATE:${c.end}`, `SUMMARY:${icsEsc(c.title)}`,
    `DESCRIPTION:${icsEsc(c.details)}`, `URL:${e.url}`, "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  const url = URL.createObjectURL(new Blob([ics], { type: "text/calendar;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = `팬싸_${e.artist.replace(/[\\/:*?"<>|\s]+/g, "_")}_${e.event_date}.ics`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
document.addEventListener("click", ev => {
  const a = ev.target.closest("[data-ics]");
  if (!a) return;
  ev.preventDefault();
  const e = CAL_CARDS[Number(a.dataset.ics)];
  if (e) downloadIcs(e);
  a.closest("details")?.removeAttribute("open");
});

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
    <div class="right">${dueHtml(e)}<div class="acts">${calHtml(e)}<a class="open" href="${esc(e.url)}" target="_blank" rel="noopener">${many ? `원문 확인 ${e.items.length}개 ↗` : "원문 확인 ↗"}</a></div></div>
  </article>`;
}

const TAB_NAME = { open: "응모 중", today: "오늘 마감", closed: "응모 마감", new: "NEW · 새로 뜬 팬싸", cal: "캘린더" };

function renderList() {
  CAL_CARDS.length = 0;
  renderCalendar();
  if (state.tab === "cal" && !state.calDay) { $("#countLine").textContent = "날짜를 누르면 그날 팬싸를 아래에 보여 줘요."; $("#list").innerHTML = ""; return; }
  const raw = filtered();
  const list = mergeCards(raw);
  $("#countLine").textContent = state.tab === "cal"
    ? `${parseDate(state.calDay).getMonth() + 1}월 ${parseDate(state.calDay).getDate()}일 · 공지 ${raw.length}건 · 카드 ${list.length}장`
    : `${TAB_NAME[state.tab]} · 공지 ${raw.length}건 · 카드 ${list.length}장 · ${state.tab === "today" ? "마감 시각순" : "행사 날짜순"}`;
  if (!list.length) {
    $("#list").innerHTML = `<div class="empty">${state.events.length ? (state.tab === "today" ? "오늘 응모가 끝나는 팬싸가 없어요." : state.tab === "closed" ? "응모 마감된 팬싸 중 조건에 맞는 게 없어요." : state.tab === "new" ? "24시간 안에 새로 뜬 팬싸가 아직 없어요." : state.tab === "cal" ? "이 날은 조건에 맞는 팬싸가 없어요." : "조건에 맞는 응모 중 팬싸가 없어요. 필터를 바꿔 보세요.") : "지금 확인된 팬싸 공지가 없어요."}</div>`;
    return;
  }
  if (state.tab === "today") {
    // 오늘 마감: 행사 날짜 대신 마감 시각이 빠른 순으로 한 묶음
    list.sort((a, b) => String(a.apply_end).localeCompare(String(b.apply_end)));
    const d = kstNow();
    $("#list").innerHTML = `<section class="group"><div class="gh today"><span class="d">${d.getMonth() + 1}.${String(d.getDate()).padStart(2, "0")}</span><span class="w">${WEEK[d.getDay()]}요일</span><span class="dd">오늘 마감 · ${list.length}건</span></div>${list.map(cardHtml).join("")}</section>`;
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
  favs = [...new Set(favs)];
  if (LOCAL) state.config = await api("/api/favorites", { favorites: favs });
  else { localStorage.setItem(FAV_KEY, JSON.stringify(favs)); state.config = { ...state.config, favorites: favs }; }
  renderFavs(); renderList(); renderTelegram();
}

// ───────── 텔레그램 봇 안내 ─────────
// 관심 아티스트를 봇에 맡겨 두고 받은 코드로 t.me/봇?start=코드 → 봇에서 「시작」만 누르면 등록된다
const tgLink = { key: "", code: "" };
async function botLink(favs) {
  const key = JSON.stringify(favs);
  if (!favs.length) return `https://t.me/${BOT}`;
  if (tgLink.key !== key) {
    tgLink.key = key; tgLink.code = "";
    try {
      const r = await fetch(`${BOT_API}/link`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ artists: favs }) });
      tgLink.code = (await r.json()).code || "";
    } catch { tgLink.code = ""; }
  }
  return tgLink.code ? `https://t.me/${BOT}?start=${tgLink.code}` : `https://t.me/${BOT}`;
}

function renderTelegram() {
  const favs = state.config.favorites || [];
  const addCmd = favs.length ? `/add ${favs.join(", ")}` : "/add 아이브, NMIXX";
  $("#tgBody").innerHTML = `<p class="hint">새 팬싸 공지가 올라오면 텔레그램 봇이 바로 알려 드려요. 관심 아티스트만 골라 받을 수 있어요.</p>
    <a class="btn tgbtn" id="tgOpen" href="https://t.me/${BOT}" target="_blank" rel="noopener">${favs.length ? `관심 아티스트 ${favs.length}명으로 알림 받기` : "텔레그램에서 알림 받기"}</a>
    <ol class="steps">
      <li>버튼을 누르면 텔레그램에서 <b>@${BOT}</b>이 열려요. <b>시작</b>을 눌러 주세요.</li>
      ${favs.length
        ? `<li>위 관심 아티스트가 봇에 바로 등록돼요. 나중에 바꾸려면 봇에서 <code>/list</code>를 보내요.</li>`
        : `<li>받고 싶은 아티스트를 봇에 보내요. 예: <code>/add 아이브, NMIXX</code><br><span class="hint" style="margin:0">위에서 관심 아티스트를 먼저 고르면 시작만 눌러도 등록돼요.</span></li>`}
    </ol>
    <div class="tgcmd"><code id="tgCmd">${esc(addCmd)}</code><button type="button" class="btn sub" id="tgCopy">복사</button></div>
    <details class="tghelp"><summary>봇 명령어</summary>
      <ul>
        <li><code>/add 이름</code> 아티스트 추가 (쉼표로 여러 명)</li>
        <li><code>/list</code> 내 목록 · 버튼으로 빼기</li>
        <li><code>/del 이름</code> 빼기</li>
        <li><code>/now</code> 지금 응모 중인 아티스트</li>
        <li><code>/all</code> 모든 새 공지 받기 (켜기/끄기)</li>
        <li><code>/overseas</code> 해외 팬싸도 받기 (켜기/끄기)</li>
        <li><code>/stop</code> 알림 그만 받기</li>
      </ul>
    </details>
    <p class="hint" style="margin-top:10px">1시간마다 새 공지를 확인해요. 응모가 끝난 공지는 보내지 않아요.</p>
    <p class="hint">🔒 봇에 저장되는 건 텔레그램 채팅 ID와 등록한 아티스트뿐이에요. 봇에 <code>/stop</code>을 보내면 바로 지워져요.</p>`;
  botLink(favs).then(href => { const a = $("#tgOpen"); if (a) a.href = href; });
}

// ───────── 판매처 상태 ─────────
function renderStores() {
  const src = state.data?.sources || [];
  $("#storeList").innerHTML = src.map(s => {
    const pcAt = s.status === "pc" && /\((\d{4}-\d\d-\d\dT\d\d:\d\d)/.exec(s.message || "");
    const label = s.status === "pc" ? `${s.count}건 · PC ${pcAt ? md(new Date(pcAt[1] + "+09:00")) + " " + hm(new Date(pcAt[1] + "+09:00")) : ""}` :
      s.status === "ok" ? `${s.count}건` : s.carried ? `${STATUS_TEXT[s.status]} · 이전 ${s.carried}건 유지` : STATUS_TEXT[s.status] || s.status;
    const bad = ["blocked", "error", "parse_error"].includes(s.status);
    return `<li title="${esc(s.message || "")}"><span class="dot st-${s.status}"></span><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.store)}</a><span class="n${bad ? " bad" : ""}">${esc(label)}</span></li>`;
  }).join("");
  const d = state.data;
  $("#tookLine").textContent = d?.checked_at ? "1시간마다 자동으로 확인해요." : "";
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
    else if (t.id === "refreshBtn") {
      if (LOCAL) { await api("/api/collect", {}); pollUntilDone(); }
      else { t.disabled = true; await load(); t.disabled = false; toast("최신 목록을 불러왔어요."); }
    }
    else if (t.id === "tgCopy") { await navigator.clipboard.writeText($("#tgCmd").textContent); toast("복사했어요. 봇 대화창에 붙여 넣어 주세요."); }
  } catch (e) { toast(e.message); t.disabled = false; }
});

document.addEventListener("change", async (ev) => {
  const t = ev.target;
  try {
    if (t.id === "storeSel") { state.store = t.value; renderList(); }
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
    }
  } catch (e) { toast(e.message); }
});

$("#q").addEventListener("input", e => { state.q = e.target.value; renderList(); });
$("#hideOverseas").classList.toggle("on", state.hideOverseas);
setInterval(renderSync, 60000);
setInterval(() => { if (!$("#refreshBtn").disabled) load(); }, 10 * 60000);
load();
