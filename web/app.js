// Data + DOM layer. Polls the bot's API and paints the cockpit.
// API base resolution order: ?api= query param -> localStorage -> none (demo).

const SYMS = {
  cryBTCUSD: { name: "BTC", tag: "Bitcoin", glow: "#F7931A" },
  R_75:      { name: "V75", tag: "Volatility 75", glow: "#22E3FF" },
  R_100:     { name: "V100", tag: "Volatility 100", glow: "#B4FF3D" },
  R_25:      { name: "V25", tag: "Volatility 25", glow: "#FF3D9A" },
  frxXAUUSD: { name: "GOLD", tag: "XAU/USD", glow: "#FFD23D" },
};
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 15000;

function apiBase() {
  const q = new URLSearchParams(location.search).get("api");
  if (q) { localStorage.setItem("hd_api", q.replace(/\/$/, "")); }
  return localStorage.getItem("hd_api") || "";
}

// ---- sample data so the page is alive before you connect --------------------
const DEMO_STATUS = {
  alive: true, mode: "live-demo", balance: 10214.4, day_start_balance: 10090,
  daily_pnl: 124.4, symbols: [
    { symbol: "cryBTCUSD", bias: "LONG", ready: true },
    { symbol: "R_75", bias: "SHORT", ready: true }],
  position: { symbol: "cryBTCUSD", direction: "LONG", entry: 63120.5, stop: 62760, target: 64080, rr: 3.0 },
  last_candle: Math.floor(Date.now() / 1000) - 300, uptime_seconds: 51840,
};
const DEMO_TRADES = { open: 1, summary: { n: 24, wins: 14, win: 0.583, total_r: 11.4, max_dd: 8.2, pnl: 214.4 },
  trades: [
    { symbol: "R_75", won: true, r: 3.0, pnl: 61.2 }, { symbol: "cryBTCUSD", won: false, r: -1.0, pnl: -40.1 },
    { symbol: "cryBTCUSD", won: true, r: 3.0, pnl: 58.9 }, { symbol: "R_75", won: true, r: 2.6, pnl: 49.0 },
    { symbol: "cryBTCUSD", won: false, r: -1.0, pnl: -41.0 }] };

// ---- helpers ----------------------------------------------------------------
const fmtMoney = (n) => Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtNum = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d }));
function ago(sec) {
  if (!sec) return "—";
  const s = Math.floor(Date.now() / 1000) - sec;
  if (s < 60) return s + "s ago"; if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago";
}
function dur(s) {
  if (!s) return "—"; const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

let animBal = null;
function countTo(el, to) {
  const from = animBal == null ? to : animBal; animBal = to;
  const t0 = performance.now(), durMs = 700;
  function step(t) {
    const k = Math.min(1, (t - t0) / durMs), e = 1 - Math.pow(1 - k, 3);
    el.textContent = fmtMoney(from + (to - from) * e);
    if (k < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ---- render -----------------------------------------------------------------
function setPill(state, text) {
  const p = $("statusPill"); p.dataset.state = state; $("statusText").textContent = text;
}

function renderStatus(s, demo) {
  countTo($("balance"), s.balance);
  const pnl = s.daily_pnl || 0, up = pnl >= 0;
  const chip = $("pnlChip"); chip.classList.toggle("down", !up);
  $("pnlArrow").textContent = up ? "▲" : "▼";
  $("dailyPnl").textContent = (up ? "+$" : "−$") + fmtMoney(Math.abs(pnl));
  $("modeChip").textContent = s.mode === "live-demo" ? "demo · live orders" : "paper";
  $("uptimeChip").textContent = "uptime " + dur(s.uptime_seconds);

  // symbols + net mood
  const wrap = $("symbols"); wrap.innerHTML = "";
  let moodSum = 0, moodN = 0;
  for (const sy of s.symbols) {
    const meta = SYMS[sy.symbol] || { name: sy.symbol, tag: "", glow: "#8A5BFF" };
    const b = sy.bias || "none";
    if (b === "LONG") { moodSum += 1; moodN++; } else if (b === "SHORT") { moodSum -= 1; moodN++; }
    const arrow = b === "LONG" ? "▲" : b === "SHORT" ? "▲" : "•";
    const sub = b === "none" ? "no clear structure" : `1H structure is ${b === "LONG" ? "bullish" : "bearish"}`;
    const el = document.createElement("div");
    el.className = "sym glass"; el.style.setProperty("--glow", meta.glow);
    el.innerHTML = `
      <div class="sym-top">
        <div><div class="sym-name">${meta.name}</div><div class="sym-tag">${meta.tag}</div></div>
        <div class="ready-dot ${sy.ready ? "on" : ""}"><i></i>${sy.ready ? "armed" : "warming"}</div>
      </div>
      <div class="bias" data-b="${b}">
        <span class="arrow">${arrow}</span>
        <span class="bias-word">${b === "none" ? "FLAT" : b}</span>
      </div>
      <div class="bias-sub">${sub}</div>`;
    wrap.appendChild(el);
  }
  if (window.cockpit) window.cockpit.setMood(moodN ? moodSum / moodN : 0);

  // position
  const card = $("positionCard"), open = !!s.position;
  card.dataset.open = open; $("pcFlat").hidden = open; $("pcLive").hidden = !open;
  if (open) {
    const p = s.position, meta = SYMS[p.symbol] || { name: p.symbol };
    $("pcSym").textContent = meta.name;
    const dir = $("pcDir"); dir.textContent = p.direction; dir.dataset.d = p.direction;
    $("pcEntry").textContent = fmtNum(p.entry, 2); $("pcStop").textContent = fmtNum(p.stop, 2);
    $("pcTarget").textContent = fmtNum(p.target, 2); $("pcRR").textContent = fmtNum(p.rr, 2) + "R";
  }

  const state = demo ? "demo" : (s.alive ? "live" : "down");
  setPill(state, demo ? "demo preview" : (s.mode === "live-demo" ? "live · demo acct" : "paper mode"));
  $("footStatus").textContent =
    (demo ? "Showing sample data — connect your bot via ⚙ · " : "Connected · ") +
    "last candle " + ago(s.last_candle) + " · refreshed " + new Date().toLocaleTimeString();
}

function renderTrades(t) {
  const sum = t.summary || { n: 0 };
  $("winRate").textContent = sum.n ? Math.round((sum.win || 0) * 100) + "%" : "—";
  $("tradeCount").textContent = sum.n || "0";
  $("totalR").textContent = sum.n ? (sum.total_r >= 0 ? "+" : "") + fmtNum(sum.total_r, 1) : "—";
  $("maxDD").textContent = sum.n ? fmtNum(sum.max_dd, 1) + "%" : "—";
  $("feedCount").textContent = (sum.n || 0) + " closed" + (t.open ? ` · ${t.open} open` : "");

  // per-symbol live win% for the compare bars
  const bySym = {};
  for (const tr of t.trades || []) { (bySym[tr.symbol] ||= []).push(tr); }
  const winPct = (arr) => arr && arr.length ? Math.round(arr.filter(x => x.won).length / arr.length * 100) : null;
  const setBar = (barId, txtId, pct) => {
    const w = pct == null ? 0 : pct;
    $(barId).style.setProperty("--w", w + "%");
    $(txtId).textContent = pct == null ? "no data" : pct + "%";
  };
  setBar("cmpBtcLive", "cmpBtcLiveTxt", winPct(bySym.cryBTCUSD));
  setBar("cmpV75Live", "cmpV75LiveTxt", winPct(bySym.R_75));

  const rows = $("feedRows");
  if (!t.trades || !t.trades.length) {
    rows.innerHTML = `<div class="empty">No closed trades yet — SLP is selective. Quiet is normal.</div>`;
    return;
  }
  rows.innerHTML = "";
  for (const tr of t.trades.slice(0, 12)) {
    const meta = SYMS[tr.symbol] || { name: tr.symbol };
    const win = (tr.pnl ?? 0) >= 0;
    const el = document.createElement("div");
    el.className = "frow";
    el.innerHTML = `
      <div class="fdir" data-d="${tr.direction || (win ? "LONG" : "SHORT")}">${(tr.direction || "•")[0]}</div>
      <div><div class="fsym">${meta.name}</div><div class="ftime">${tr.won ? "target hit" : "stopped"}</div></div>
      <div class="fr-r ${(tr.r ?? 0) >= 0 ? "pos" : "neg"}">${tr.r == null ? "" : (tr.r >= 0 ? "+" : "") + fmtNum(tr.r, 1) + "R"}</div>
      <div class="fpnl ${win ? "pos" : "neg"}">${win ? "+" : "−"}$${fmtMoney(Math.abs(tr.pnl || 0))}</div>`;
    rows.appendChild(el);
  }
}

// ---- polling ----------------------------------------------------------------
async function tickData() {
  const base = apiBase();
  if (!base) { renderStatus(DEMO_STATUS, true); renderTrades(DEMO_TRADES); return; }
  try {
    const [st, tr] = await Promise.all([
      fetch(base + "/api/status", { cache: "no-store" }).then(r => r.json()),
      fetch(base + "/api/trades", { cache: "no-store" }).then(r => r.json()),
    ]);
    renderStatus(st, false); renderTrades(tr);
  } catch (e) {
    setPill("down", "can't reach bot");
    $("footStatus").textContent = "Connection failed — check the URL in ⚙ (is the Render service awake?)";
  }
}

// ---- settings modal ---------------------------------------------------------
const modal = $("modal");
function openModal() { $("apiInput").value = localStorage.getItem("hd_api") || ""; modal.hidden = false; $("apiInput").focus(); }
$("settingsBtn").onclick = openModal;
$("closeModal").onclick = () => (modal.hidden = true);
$("saveApi").onclick = () => {
  const v = $("apiInput").value.trim().replace(/\/$/, "");
  if (v) localStorage.setItem("hd_api", v); else localStorage.removeItem("hd_api");
  modal.hidden = true; tickData();
};
modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
if (!apiBase()) $("modalHint").textContent = "No bot connected yet — you're seeing a sample preview.";

// go
tickData();
setInterval(tickData, REFRESH_MS);
