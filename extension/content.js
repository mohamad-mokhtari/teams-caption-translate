/*
 * Phase 0 — caption capture only.
 *
 * Purpose: prove we can read Teams live captions reliably from the DOM.
 * Deliberately does NO translation and makes NO network requests. If this phase
 * fails, the whole approach changes (we'd fall back to capturing audio and
 * running ASR ourselves), so it is worth answering on its own.
 *
 * Two problems this has to solve, and they are the real work:
 *
 *  1. FINDING the captions. Teams' markup changes and is obfuscated, so nothing
 *     here hardcodes a class name as the only strategy. We try known attributes
 *     first, then fall back to letting the user click the caption area once and
 *     remembering it. A selector we can re-point in a minute beats a clever
 *     guess that silently stops matching.
 *
 *  2. CHURN. Live ASR rewrites a line as it revises its hypothesis:
 *        "I think we should" -> "I think we should deploy"
 *        -> "I think we shouldn't deploy on Friday"
 *     Translating every revision would flicker, cost money, and sometimes invert
 *     the meaning. We only emit a segment once it has stopped changing.
 */

(() => {
  "use strict";
  if (window.__mctLoaded) return;
  window.__mctLoaded = true;

  // all_frames is on, because Teams may render captions inside an iframe. Only the
  // top frame draws the panel; child frames capture and post their findings up.
  const IS_TOP = window.top === window;

  const SETTLE_MS = 700;   // no change for this long => the line is final
  const MAX_ROWS  = 200;

  // The translation companion, running on this same machine. 127.0.0.1 rather than
  // a LAN address on purpose: the service holds an API key and has no auth, and a
  // browser treats localhost as a trustworthy origin, so an https page may call it
  // without tripping mixed-content rules.
  const SERVER = "http://127.0.0.1:8100";
  const CONTEXT_N = 3;     // preceding segments sent so pronouns resolve

  const server = {
    ok: false,
    rtl: false,
    lang: "",
    lastError: "",
    inflight: 0,
    lastMs: 0,
  };

  /** Recent English segments, for context. Kept short — the point is resolving
   *  "he"/"it"/"that", not summarising the meeting. */
  const recent = [];

  /**
   * Everything each speaker said, in order. Feeds the per-speaker summary.
   * English is stored, not the translation: summarising a translation compounds
   * whatever the translator got wrong.
   */
  const bySpeaker = new Map();   // speaker -> [text, ...]

  /**
   * Speaker colours.
   *
   * Derived from a hash of the name rather than assigned randomly, so the same
   * person keeps the same colour for the whole meeting, across a page reload, and
   * on everyone's screen. Random assignment would also occasionally pick two
   * near-identical colours for the two people talking most.
   *
   * The palette is hand-picked for legibility on the dark panel; generating hues
   * arbitrarily produces some that are unreadable against it.
   */
  const PALETTE = [
    "#60a5fa", "#f87171", "#34d399", "#fbbf24", "#c084fc",
    "#22d3ee", "#fb923c", "#a3e635", "#f472b6", "#94a3b8",
  ];
  const colorCache = new Map();
  function colorFor(name) {
    const key = (name || "").trim() || "unknown";
    if (!colorCache.has(key)) {
      let h = 0;
      for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
      colorCache.set(key, PALETTE[h % PALETTE.length]);
    }
    return colorCache.get(key);
  }

  /** "" means show everyone. */
  let speakerFilter = "";

  // Ordered by how much we trust them. Attribute/data hooks survive CSS churn
  // better than generated class names.
  // Confirmed against Teams web on 2026-08-29: caption text lives in
  // <span class="fui-StyledText">, inside a container whose class is generated
  // (e.g. .___18l92v8) and therefore useless as a selector.
  //
  // fui-* is Fluent UI's stable naming, so it survives far better than the hashed
  // classes around it — but it is used all over Teams, not only for captions. So we
  // look for the span, then attach to its container, rather than trusting it alone.
  const CANDIDATES = [
    '[data-tid="closed-caption-v2-window"] span.fui-StyledText',
    '[data-tid="closed-caption-v2-window"]',
    '[data-tid="closed-caption-renderer-wrapper"]',
    '[data-tid*="closed-caption"]',
    '[class*="closedCaption"]',
    '[class*="caption"]',
    '[aria-label*="aption"]',
  ];

  /**
   * Is this node part of our own UI?
   *
   * The panel displays the captions it captures, so any text search will find that
   * text inside the panel and — without this guard — attach to it. The result looks
   * plausible (rows of speakers and utterances) but it is the tool reading its own
   * output, and real captions stop arriving. Every search path must exclude it.
   */
  const isOurs = (el) => !!(el && el.closest && el.closest("#mct-panel"));

  const state = {
    container: null,
    observer: null,
    /** key -> {speaker, text, el, timer, emitted} */
    pending: new Map(),
    metaMsg: "looking for captions\u2026",
    emitted: 0,
    started: Date.now(),
  };

  // ---------- panel -------------------------------------------------------

  // Styles are injected from here rather than a separate .css file. A missing
  // stylesheet made the panel render invisibly at the bottom of the page flow —
  // a silent failure that cost an afternoon. One file, one thing to go wrong.
  const style = document.createElement("style");
  style.textContent = "#mct-panel {\n  position: fixed;\n  right: 16px;\n  bottom: 16px;\n  width: 380px;\n  max-height: 55vh;\n  display: flex;\n  flex-direction: column;\n  background: #14161c;\n  color: #e8e8ea;\n  border: 1px solid #2a2f3a;\n  border-radius: 10px;\n  font: 13px/1.5 ui-sans-serif, system-ui, sans-serif;\n  z-index: 2147483647;          /* above the page's own overlays */\n  box-shadow: 0 8px 28px rgba(0,0,0,.45);\n}\n#mct-head {\n  display: flex; align-items: center; gap: 8px;\n  padding: 8px 10px; border-bottom: 1px solid #2a2f3a;\n  font-weight: 600; cursor: move; user-select: none;\n}\n#mct-dot { width: 8px; height: 8px; border-radius: 50%; background: #6b7280; }\n#mct-dot.live { background: #34d399; }\n#mct-head .sp { flex: 1; }\n#mct-head button {\n  background: #232833; color: #e8e8ea; border: 1px solid #333a49;\n  border-radius: 6px; padding: 2px 8px; font: inherit; font-size: 12px; cursor: pointer;\n}\n#mct-log { overflow-y: auto; padding: 8px 10px; }\n.mct-seg { margin-bottom: 8px; }\n.mct-spk { color: #818cf8; font-weight: 600; font-size: 12px; }\n.mct-txt { white-space: pre-wrap; word-break: break-word; }\n.mct-live { opacity: .55; font-style: italic; }   /* still changing */\n.mct-meta { color: #7b8194; font-size: 11px; padding: 6px 10px; border-top: 1px solid #2a2f3a; }\n#mct-picking * { outline: 2px dashed #f59e0b !important; cursor: crosshair !important; }\n\n\n/* Translation lane. Persian, Arabic and Hebrew render right-to-left; without dir\n   the output is technically correct and practically unreadable. Tahoma is a safe\n   Persian face on Windows, which is what most of the team uses. */\n.mct-tr {\n  margin-top: 2px;\n  padding-left: 8px;\n  border-left: 2px solid #34d399;\n  color: #d7f5e6;\n}\n.mct-tr:empty { display: none; }\n.mct-tr.rtl {\n  direction: rtl;\n  text-align: right;\n  padding-left: 0;\n  padding-right: 8px;\n  border-left: 0;\n  border-right: 2px solid #34d399;\n  font-family: Tahoma, \"Segoe UI\", \"Noto Naskh Arabic\", sans-serif;\n  font-size: 14px;\n}\n.mct-tr.err { color: #f87171; border-color: #f87171; font-style: italic; font-size: 12px; }\n.mct-lat { font-size: 10px; color: #6b7280; margin-top: 1px; }\n.mct-lat:empty { display: none; }\n#mct-dot.warn { background: #f59e0b; }\n\n\n/* --- resizing ------------------------------------------------------------\n   Default small so it does not cover the meeting; maximised when you actually\n   need to read along. The native resize handle covers everything in between. */\n#mct-panel { resize: both; overflow: hidden; min-width: 260px; min-height: 120px; }\n#mct-panel.max { width: 620px; max-height: 82vh; }\n\n/* --- tabs ---------------------------------------------------------------- */\n.mct-tab {\n  font-size: 12px; font-weight: 600; padding: .1rem .5rem;\n  border-radius: 6px; cursor: pointer; color: var(--muted, #9096a3);\n}\n.mct-tab.on { background: #232833; color: #e8e8ea; }\n\n/* --- speaker chips: colour key and filter -------------------------------- */\n#mct-speakers {\n  display: flex; flex-wrap: wrap; gap: 4px;\n  padding: 6px 10px 0; max-height: 4.5rem; overflow-y: auto;\n}\n#mct-speakers:empty { display: none; }\n.mct-chip {\n  font-size: 11px; font-weight: 600; padding: .05rem .45rem;\n  border: 1px solid #333a49; border-radius: 20px;\n  cursor: pointer; white-space: nowrap; opacity: .65;\n}\n.mct-chip:hover { opacity: 1; }\n.mct-chip.on { opacity: 1; background: rgba(255,255,255,.07); }\n\n/* --- summary view -------------------------------------------------------- */\n#mct-summary { display: none; padding: 8px 10px; overflow-y: auto; flex: 1; }\n.mct-srow { display: flex; gap: 6px; margin-bottom: 8px; }\n.mct-srow select {\n  flex: 1; background: #14161c; color: #e8e8ea;\n  border: 1px solid #333a49; border-radius: 6px; padding: .25rem; font: inherit; font-size: 12px;\n}\n.mct-sum-out { white-space: pre-wrap; word-break: break-word; line-height: 1.6; }\n.mct-sum-out.rtl {\n  direction: rtl; text-align: right;\n  font-family: Tahoma, \"Segoe UI\", \"Noto Naskh Arabic\", sans-serif; font-size: 14px;\n}\n.mct-sum-out.err { color: #f87171; font-style: italic; }\n\n\n/* --- layout ---------------------------------------------------------------\n   The panel is a flex column. Without an explicit flex on the scrolling areas,\n   the browser sizes them from content: the log stops growing into the space it\n   has, and the chip row gets clipped mid-row so the bottom line of chips is cut\n   in half. min-height:0 is required for a flex child to be allowed to shrink\n   below its content size and scroll instead of overflowing. */\n#mct-log      { flex: 1 1 auto; min-height: 0; }\n#mct-summary  { flex: 1 1 auto; min-height: 0; flex-direction: column; }\n#mct-head     { flex: 0 0 auto; flex-wrap: wrap; }\n.mct-meta     { flex: 0 0 auto; }\n\n/* Chips wrap freely rather than scrolling inside a fixed height \u2014 a scrollable\n   box cut rows in half, which is what looked broken. */\n#mct-speakers {\n  flex: 0 0 auto;\n  max-height: none;\n  overflow: visible;\n  padding-bottom: 6px;\n}\n.mct-chip { line-height: 1.5; }\n\n\n/* Launcher pill, shown while the panel is closed. Deliberately small and dim: it\n   is a way back in, not something to look at during a meeting. */\n#mct-launcher {\n  position: fixed; right: 16px; bottom: 16px;\n  display: none;\n  background: #14161c; color: #9096a3;\n  border: 1px solid #2a2f3a; border-radius: 20px;\n  padding: .3rem .8rem;\n  font: 12px/1.4 ui-sans-serif, system-ui, sans-serif;\n  cursor: pointer; z-index: 2147483647;\n  box-shadow: 0 4px 14px rgba(0,0,0,.35);\n}\n#mct-launcher:hover { color: #e8e8ea; border-color: #3b4252; }\n\n#mct-close { color: #f87171; }\n";
  (document.head || document.documentElement).appendChild(style);

  /**
   * Built with createElement rather than innerHTML.
   *
   * Teams sets a Trusted Types CSP (`require-trusted-types-for 'script'`), which
   * makes ANY innerHTML assignment throw:
   *   "This document requires 'TrustedHTML' assignment. The action has been blocked."
   * There is no way around it from a normal script, and no reason to want one —
   * createElement + textContent is safer anyway, and immune to the problem.
   */
  function el(tag, props = {}, kids = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "text") n.textContent = v;
      else if (k === "cls") n.className = v;
      else n.setAttribute(k, v);
    }
    for (const kid of kids) n.appendChild(kid);
    return n;
  }

  const panel = el("div", { id: "mct-panel" }, [
    el("div", { id: "mct-head" }, [
      el("span", { id: "mct-dot" }),
      el("span", { id: "mct-tab-live",  cls: "mct-tab on", text: "Live" }),
      el("span", { id: "mct-tab-sum",   cls: "mct-tab",    text: "Summary" }),
      el("span", { cls: "sp" }),
      el("button", { id: "mct-pick",  text: "pick" }),
      el("button", { id: "mct-diag",  text: "find" }),
      el("button", { id: "mct-dump",  text: "dump" }),
      el("button", { id: "mct-retry", text: "reconnect" }),
      el("button", { id: "mct-copy",  text: "copy" }),
      el("button", { id: "mct-max",   text: "\u2921", title: "maximise / restore" }),
      el("button", { id: "mct-hide",  text: "\u2013", title: "collapse" }),
      el("button", { id: "mct-close", text: "\u00d7", title: "close" }),
    ]),

    // Speaker chips: colour key, and the filter control. Clicking one shows only
    // that person; "All" restores the full conversation.
    el("div", { id: "mct-speakers" }),

    el("div", { id: "mct-log" }),

    // Summary view, hidden until its tab is selected.
    el("div", { id: "mct-summary" }, [
      el("div", { cls: "mct-srow" }, [
        el("select", { id: "mct-sum-who" }),
        el("button", { id: "mct-sum-go", text: "Summarise" }),
      ]),
      el("div", { id: "mct-sum-out", cls: "mct-sum-out" }),
    ]),

    el("div", { id: "mct-meta", cls: "mct-meta", text: "looking for captions\u2026" }),
  ]);
  /**
   * Small pill shown while the panel is closed.
   *
   * Without it, closing the panel would be a one-way door: the only way back would
   * be reloading the page, which also throws away the transcript. It doubles as the
   * way in when auto-detect has not found captions yet and you need "find".
   */
  const launcher = el("div", { id: "mct-launcher", text: "\u25cf captions" });
  launcher.onclick = () => setPanelOpen(true, /*byUser=*/true);

  /**
   * Open and close only ever toggle visibility — the panel is never removed and its
   * rows are never cleared. Closing and reopening therefore keeps the whole
   * conversation, which is the point: people close it to see the meeting, not to
   * discard what was said.
   */
  let panelOpen = false;
  let userClosed = false;      // an explicit close must not be undone by the poller

  function setPanelOpen(open, byUser = false) {
    panelOpen = open;
    if (byUser) userClosed = !open;
    panel.style.display    = open ? "flex" : "none";
    launcher.style.display = open ? "none" : "block";
    if (open) $log.scrollTop = $log.scrollHeight;
  }

  function mountPanel() {
    if (!IS_TOP) return;
    const host = document.body || document.documentElement;
    if (!host) { setTimeout(mountPanel, 300); return; }
    if (!panel.isConnected) host.appendChild(panel);
    if (!launcher.isConnected) host.appendChild(launcher);
    setPanelOpen(false);          // stays closed until captions appear
    // Teams is a single-page app and re-renders large parts of the DOM; if it
    // removes our node, put it back.
    setInterval(() => {
      const host2 = document.body || document.documentElement;
      if (!panel.isConnected) host2.appendChild(panel);
      if (!launcher.isConnected) host2.appendChild(launcher);
    }, 3000);
    console.log("[caption] panel mounted");
  }
  mountPanel();

  const $log  = panel.querySelector("#mct-log");
  const $meta = panel.querySelector("#mct-meta");
  const $dot   = panel.querySelector("#mct-dot");
  const $chips = panel.querySelector("#mct-speakers");
  const $sum   = panel.querySelector("#mct-summary");
  const $sumWho = panel.querySelector("#mct-sum-who");
  const $sumOut = panel.querySelector("#mct-sum-out");

  const transcript = [];   // in memory only; nothing is persisted

  function meta(msg) { state.metaMsg = msg; paintStatus(); }

  /** One place that writes the footer, so capture state and translator state
   *  cannot fight over it. */
  function paintStatus() {
    const bits = [];
    if (state.metaMsg) bits.push(state.metaMsg);
    if (server.ok) {
      bits.push(`→ ${server.lang}`);
      if (server.inflight) bits.push(`translating ${server.inflight}…`);
      else if (server.lastMs) bits.push(`${Math.round(server.lastMs)}ms`);
    } else {
      bits.push(server.lastError || "translator offline");
    }
    $meta.textContent = bits.join(" · ");
    $dot.classList.toggle("live", !!state.container);
    $dot.classList.toggle("warn", !!state.container && !server.ok);
  }

  function render(key, speaker, text, final) {
    let row = $log.querySelector(`[data-k="${CSS.escape(key)}"]`);
    if (!row) {
      row = el("div", { cls: "mct-seg" }, [
        el("div", { cls: "mct-spk" }),
        el("div", { cls: "mct-txt" }),
        el("div", { cls: "mct-tr" + (server.rtl ? " rtl" : "") }),
        el("div", { cls: "mct-lat" }),
      ]);
      row.dataset.k = key;
      $log.appendChild(row);
      while ($log.children.length > MAX_ROWS) $log.firstChild.remove();
    }
    const spk = row.querySelector(".mct-spk");
    spk.textContent = speaker || "";
    spk.style.color = colorFor(speaker);
    row.dataset.spk = speaker || "";
    // A row created while a filter is active must respect it immediately, or the
    // filtered view silently gains rows as people keep talking.
    row.style.display = (!speakerFilter || speakerFilter === speaker) ? "" : "none";
    const t = row.querySelector(".mct-txt");
    t.textContent = text;
    t.className = "mct-txt" + (final ? "" : " mct-live");
    $log.scrollTop = $log.scrollHeight;
  }

  /** Chips double as the colour key and the filter control. */
  function renderChips() {
    $chips.textContent = "";
    const names = [...bySpeaker.keys()].filter(Boolean);
    if (!names.length) return;

    const all = el("span", {
      cls: "mct-chip" + (speakerFilter ? "" : " on"),
      text: `All (${names.length})`,
    });
    all.onclick = () => setFilter("");
    $chips.appendChild(all);

    for (const n of names) {
      const c = el("span", {
        cls: "mct-chip" + (speakerFilter === n ? " on" : ""),
        text: `${n} (${bySpeaker.get(n).length})`,
      });
      c.style.color = colorFor(n);
      c.style.borderColor = colorFor(n);
      c.onclick = () => setFilter(speakerFilter === n ? "" : n);
      $chips.appendChild(c);
    }
  }

  function setFilter(name) {
    speakerFilter = name;
    for (const row of $log.querySelectorAll(".mct-seg")) {
      row.style.display = (!name || row.dataset.spk === name) ? "" : "none";
    }
    renderChips();
    meta(name ? `showing only ${name}` : "showing everyone");
    $log.scrollTop = $log.scrollHeight;
  }

  /** Record what a speaker said, and keep the chips and dropdown in step. */
  function remember(speaker, text, revised) {
    const who = speaker || "";
    if (!bySpeaker.has(who)) bySpeaker.set(who, []);
    const arr = bySpeaker.get(who);
    if (revised && arr.length) arr[arr.length - 1] = text;
    else arr.push(text);
    renderChips();
    syncSpeakerOptions();
  }

  function syncSpeakerOptions() {
    const names = [...bySpeaker.keys()].filter(Boolean);
    const chosen = $sumWho.value;
    $sumWho.textContent = "";
    for (const n of names) {
      $sumWho.appendChild(el("option", { value: n, text: `${n} (${bySpeaker.get(n).length})` }));
    }
    if (names.includes(chosen)) $sumWho.value = chosen;
  }

  function renderTranslation(key, text, error = "", latency = "") {
    const row = $log.querySelector(`[data-k="${CSS.escape(key)}"]`);
    if (!row) return;
    const tr = row.querySelector(".mct-tr");
    tr.textContent = error || text;
    tr.className = "mct-tr" + (server.rtl ? " rtl" : "") + (error ? " err" : "");
    row.querySelector(".mct-lat").textContent = latency;
    $log.scrollTop = $log.scrollHeight;
  }

  /** A segment stopped changing: record it, and ask for a translation. */
  function emit(key, speaker, text, revised = false) {
    if (!revised) state.emitted++;
    if (revised) {
      const last = transcript.findLast?.(r => r.key === key);
      if (last) { last.text = text; last.revised = true; }
      else transcript.push({ key, t: new Date().toISOString(), speaker, text });
    } else {
      transcript.push({ key, t: new Date().toISOString(), speaker, text });
    }
    render(key, speaker, text, true);
    console.log(`[caption]${revised ? " (revised)" : ""}`, speaker ? speaker + ":" : "", text);

    // Context is the English history, not the translations.
    if (!revised) { recent.push(text); while (recent.length > 10) recent.shift(); }
    else if (recent.length) recent[recent.length - 1] = text;

    remember(speaker, text, revised);

    requestTranslation(key, speaker, text);
    const mins = Math.max((Date.now() - state.started) / 60000, 0.01);
    // "capture only" was true in phase 0 and has been wrong since translation
    // landed. A stale status line is worse than none — it tells the reader the
    // feature they are looking for does not exist yet.
    meta(`${state.emitted} segments · ${(state.emitted / mins).toFixed(1)}/min`);
  }

  // ---------- translation --------------------------------------------------

  /**
   * Per-line sequence numbers.
   *
   * A line can be revised while its first translation is still in flight, and
   * responses can come back out of order. Without a sequence check, a slow reply
   * for "What" can overwrite the newer, correct reply for "What country?" — the
   * reader then sees a translation that silently contradicts the caption above it.
   */
  const seqOf = new Map();

  async function loadConfig() {
    try {
      const r = await fetch(`${SERVER}/config`, { method: "GET" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const cfg = await r.json();
      server.ok = true;
      server.rtl = !!cfg.rtl;
      server.lang = cfg.target_lang_name || cfg.target_lang || "";
      server.lastError = "";
      console.log("[caption] translator ready:", server.lang, server.rtl ? "(RTL)" : "");
    } catch (e) {
      server.ok = false;
      server.lastError = `translator offline at ${SERVER}`;
      console.warn("[caption]", server.lastError, e.message);
    }
    paintStatus();
  }

  async function requestTranslation(key, speaker, text) {
    if (!server.ok) return;

    const seq = (seqOf.get(key) || 0) + 1;
    seqOf.set(key, seq);

    server.inflight++;
    paintStatus();
    try {
      const r = await fetch(`${SERVER}/translate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          key, speaker, text,
          context: recent.slice(-CONTEXT_N),
        }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();

      // A newer revision of this line has been sent since; discard this reply.
      if (seqOf.get(key) !== seq) return;

      if (d.error) {
        server.lastError = d.error;
        renderTranslation(key, "", d.error);
      } else {
        server.lastError = "";
        server.lastMs = d.ms;
        renderTranslation(key, d.translation, "", d.cached ? "cached" : `${Math.round(d.ms)}ms`);
      }
    } catch (e) {
      server.ok = false;
      server.lastError = `translator unreachable — is it running on ${SERVER}?`;
      renderTranslation(key, "", server.lastError);
    } finally {
      server.inflight--;
      paintStatus();
    }
  }

  // ---------- reading the caption DOM -------------------------------------

  /**
   * Teams renders each caption line as its own element. We identify a line by a
   * stable-ish key so revisions to the SAME line update in place instead of
   * appearing as new lines.
   */
  function keyFor(el, i) {
    return el.id || el.getAttribute("data-tid") || `idx-${i}`;
  }

  function speakerOf(el) {
    const s = el.querySelector('[data-tid*="author"], [class*="author"], [class*="speaker"]');
    return s ? s.textContent.trim() : "";
  }

  function textOf(el, speaker) {
    let t = (el.innerText || "").trim();
    if (speaker && t.startsWith(speaker)) t = t.slice(speaker.length).trim();
    return t.replace(/\s+/g, " ");
  }

  /**
   * Caption extraction, driven by Teams' own semantic attributes.
   *
   * Confirmed structure (Teams web, 2026-08-29):
   *
   *   <div class="___18l92v8 ...">                       one caption line
   *     <div>
   *       <span class="fui-ChatMessageCompact__author">
   *         <span data-tid="author">Mohamad Mokhtari</span>
   *     <div>
   *       <span data-tid="closed-caption-text">Welcome to this channel.</span>
   *
   * The class names are generated (`___18l92v8`, `fod5ikn`) and will change on any
   * Teams release. The `data-tid` values are semantic and are what Microsoft's own
   * tests hook into, so they are far more durable. We key off those exclusively and
   * treat everything else as decoration.
   */
  const TEXT_SEL   = '[data-tid="closed-caption-text"]';
  const AUTHOR_SEL = '[data-tid="author"]';

  /** Stable per-element ids. Teams mutates a line's text in place as the ASR
   *  revises, so identity must follow the NODE, not its content or its index —
   *  both of which change. A WeakMap also lets removed nodes be collected. */
  const nodeIds = new WeakMap();
  let nextId = 1;
  function idOf(el) {
    if (!nodeIds.has(el)) nodeIds.set(el, "L" + nextId++);
    return nodeIds.get(el);
  }

  /** From a caption-text span, walk up to the element that holds both it and the
   *  author span — that is one logical line. */
  function lineOf(textEl) {
    let el = textEl;
    for (let i = 0; i < 6 && el.parentElement; i++) {
      el = el.parentElement;
      if (el.querySelector(AUTHOR_SEL)) return el;
    }
    return textEl.parentElement || textEl;
  }

  function scan() {
    const root = state.container || document;
    let texts;
    try {
      texts = Array.from(root.querySelectorAll(TEXT_SEL));
      // The container itself may sit above a shadow boundary.
      if (!texts.length) texts = deepQueryAll(TEXT_SEL, root).filter(e => !isOurs(e));
    } catch { return; }

    texts.forEach((textEl) => {
      if (isOurs(textEl)) return;

      const line    = lineOf(textEl);
      const authorEl = line.querySelector(AUTHOR_SEL);
      const speaker = authorEl ? authorEl.textContent.trim() : "";
      const text    = (textEl.textContent || "").trim().replace(/\s+/g, " ");
      if (!text) return;

      const key  = idOf(line);
      const prev = state.pending.get(key);
      if (prev && prev.text === text) return;        // unchanged
      if (prev?.timer) clearTimeout(prev.timer);

      render(key, speaker, text, false);             // show at once, greyed

      const timer = setTimeout(() => {
        const cur = state.pending.get(key);
        if (!cur) return;
        // A line can settle, then be revised again ("What" -> "What country?").
        // Emit the revision under the SAME key so downstream replaces rather than
        // appending a second, superseded translation.
        emit(key, cur.speaker, cur.text, cur.emitted);
        cur.emitted = true;
      }, SETTLE_MS);

      state.pending.set(key, { speaker, text, timer, emitted: prev?.emitted || false });
    });
  }

  function attach(el, how) {
    if (isOurs(el)) {
      meta("refused: that is this panel, not the Teams captions");
      console.warn("[caption] refused to attach to our own panel");
      return;
    }
    if (state.observer) state.observer.disconnect();
    state.container = el;
    state.observer = new MutationObserver(scan);
    state.observer.observe(el, { childList: true, subtree: true, characterData: true });
    $dot.classList.add("live");
    meta(`attached via ${how} — waiting for speech`);
    console.log("[caption] attached via", how, "->",
      `<${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}` +
      `${el.className ? "." + String(el.className).split(/\s+/)[0] : ""}>`);
    scan();
  }

  /** Walk the DOM including open shadow roots. querySelector stops at a shadow
   *  boundary, so anything Teams renders in a web component is invisible to it. */
  function deepQueryAll(selector, root = document, out = [], depth = 0) {
    if (depth > 12) return out;
    try { out.push(...root.querySelectorAll(selector)); } catch { /* bad selector */ }
    const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (const el of all) {
      if (el.shadowRoot) deepQueryAll(selector, el.shadowRoot, out, depth + 1);
    }
    return out;
  }

  /** The observation root must hold every caption line. Attaching to one line's
   *  own div means later lines are never seen. */
  function captionRoot() {
    // deepQueryAll pierces open shadow roots; querySelector does not. Teams renders
    // parts of the meeting UI in web components, so the plain call finds nothing
    // even when the element is plainly there in the inspector.
    const wins = deepQueryAll('[data-tid="closed-caption-v2-window"]').filter(e => !isOurs(e));
    if (wins.length) return { el: wins[0], how: 'data-tid="closed-caption-v2-window"' };

    const texts = deepQueryAll(TEXT_SEL).filter(e => !isOurs(e));
    if (!texts.length) return null;
    const line = lineOf(texts[0]);
    const root = line.parentElement || line;
    const shadow = root.getRootNode() !== document ? " (shadow DOM)" : "";
    return { el: root, how: "parent of first caption line" + shadow };
  }

  function autoFind() {
    const root = captionRoot();
    if (root && !isOurs(root.el)) { attach(root.el, root.how); return true; }
    for (const sel of CANDIDATES) {
      const hits = deepQueryAll(sel);
      const el = hits.find(h => !isOurs(h) && (h.innerText || "").trim().length);
      if (el) { attach(el, sel + (el.getRootNode() !== document ? " (shadow DOM)" : "")); return true; }
    }
    return false;
  }

  /**
   * The diagnostic that actually solves this: say a word in the meeting, type it
   * here, and we report every element containing it — including inside shadow roots
   * and this frame's own document. That tells us the real selector instead of
   * guessing at Teams' obfuscated markup.
   */
  function findByText(needle) {
    const hits = [];
    const seen = new Set();
    const walk = (root, depth = 0) => {
      if (depth > 12) return;
      const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
      for (const el of all) {
        if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
        if (isOurs(el)) continue;                     // never match our own panel
        const txt = (el.textContent || "");
        if (!txt.toLowerCase().includes(needle.toLowerCase())) continue;
        // Keep the deepest elements — the ones whose children don't also match.
        const childMatch = Array.from(el.children).some(
          c => (c.textContent || "").toLowerCase().includes(needle.toLowerCase()));
        if (childMatch) continue;
        if (seen.has(el)) continue;
        seen.add(el);
        hits.push(el);
      }
    };
    walk(document);
    return hits;
  }
  window.__mctFindByText = findByText;

  /**
   * Keep looking, indefinitely.
   *
   * The previous version gave up after 120 tries (~2 minutes) and said so in the
   * footer. In practice a meeting starts, captions get switched on, people get
   * settled — and two minutes are gone before anyone speaks. The search had already
   * quit, so nothing was ever captured and the only way back was the manual picker.
   *
   * There is no reason to stop: the poll is a single querySelectorAll against a
   * page that is doing far heavier work anyway. It just slows down after the first
   * minute so an idle tab is not scanning every second all day.
   *
   * It also re-attaches. Teams re-renders large parts of the DOM — leaving a call,
   * rejoining, toggling captions off and on — and the element we were observing can
   * be detached without warning. Watching a node that is no longer in the document
   * fails silently, which looks exactly like "captions stopped working".
   */
  let tries = 0;
  setInterval(() => {
    tries++;

    // Are captions on screen right now?
    const captionsOn = deepQueryAll(TEXT_SEL).some(e => !isOurs(e));

    if (captionsOn) {
      // Turning captions on reopens the panel — unless it was closed by hand,
      // in which case leave it closed and let the launcher pill offer the way back.
      if (!panelOpen && !userClosed) setPanelOpen(true);
    } else if (panelOpen) {
      // Captions switched off: put the panel away, but keep everything it holds.
      setPanelOpen(false);
      // An explicit close applies to this caption session only. Once captions go
      // away, the next time they come on the panel should open again — otherwise
      // closing it once silently disables the tool for the rest of the day.
      userClosed = false;
    } else {
      userClosed = false;
    }

    if (state.container) {
      // Still attached to something that is still in the page and still holds
      // captions? Then there is nothing to do.
      const alive = state.container.isConnected &&
                    (state.container.querySelector(TEXT_SEL) ||
                     deepQueryAll(TEXT_SEL, state.container).length ||
                     state.pending.size);
      if (alive) return;
      console.log("[caption] container went stale — re-attaching");
      meta("caption panel changed — re-attaching…");
      state.container = null;
      if (state.observer) state.observer.disconnect();
    }

    if (autoFind()) return;

    // After the first minute, only scan every third tick — captions are usually
    // on within seconds of someone speaking, and an idle tab should stay idle.
    if (tries > 60 && tries % 3 !== 0) return;

    if (tries % 5 === 0) {
      const seen = deepQueryAll(TEXT_SEL).length;
      meta(seen
        ? `found ${seen} caption node(s) but could not attach — try "find text"`
        : `waiting for captions… turn on live captions in Teams`);
    }
  }, 1000);

  // ---------- manual picker (the fallback that makes this robust) ----------

  panel.querySelector("#mct-pick").onclick = () => {
    meta("click the caption text in the meeting…");
    document.body.id = "mct-picking";
    const once = (ev) => {
      if (panel.contains(ev.target)) return;
      ev.preventDefault(); ev.stopPropagation();
      document.body.removeAttribute("id");
      document.removeEventListener("click", once, true);
      // Go up a couple of levels: the clicked node is usually one line, and we
      // want the container that holds all of them.
      let el = ev.target;
      for (let i = 0; i < 3 && el.parentElement; i++) el = el.parentElement;
      attach(el, "manual pick");
    };
    document.addEventListener("click", once, true);
  };

  panel.querySelector("#mct-diag").onclick = () => {
    const needle = prompt(
      "Type a word you just SAW in the Teams caption (e.g. Hello).\n" +
      "We'll locate the element containing it and attach to its container."
    );
    if (!needle) return;
    const hits = findByText(needle);
    console.log("[caption] matches for", JSON.stringify(needle), "=",
      hits.map(h => `<${h.tagName.toLowerCase()}${h.className ? "." +
        String(h.className).split(/\s+/)[0] : ""}> "${(h.innerText||"").trim().slice(0,50)}"`));
    if (!hits.length) {
      meta(`"${needle}" not found in this frame — captions may be in an iframe. See console.`);
      return;
    }
    // The caption line is the deepest match; its container is a level or two up.
    // One level up from the deepest match: enough to include the speaker node,
    // not enough to reach the ancestor that concatenates every line.
    let el = hits[hits.length - 1];
    if (el.parentElement) el = el.parentElement;
    if (el.parentElement) el = el.parentElement;   // container of the line elements
    attach(el, `found via text "${needle}"`);
    meta(`attached via text search (${hits.length} matches) — check console for details`);
  };

  /** Print the attached container's structure so selectors can be tuned against
   *  what Teams actually renders, instead of guesses. */
  // ---- tabs ---------------------------------------------------------------
  const $tabLive = panel.querySelector("#mct-tab-live");
  const $tabSum  = panel.querySelector("#mct-tab-sum");

  function showTab(which) {
    const live = which === "live";
    $tabLive.classList.toggle("on", live);
    $tabSum.classList.toggle("on", !live);
    // Explicit values, not "".
    //
    // Clearing an inline style falls back to the stylesheet, and the stylesheet says
    // `#mct-summary { display: none }` because it starts hidden. So `= ""` re-hid the
    // panel it was meant to reveal, and the tab appeared to do nothing at all.
    //
    // #mct-speakers is the exception: it keeps "" on purpose, so the `:empty` rule
    // can still hide the chip row before anyone has spoken. An inline value would
    // override that and leave an empty strip.
    $log.style.display   = live ? "block" : "none";
    $chips.style.display = live ? "" : "none";
    $sum.style.display   = live ? "none" : "flex";
    if (!live) syncSpeakerOptions();
  }
  $tabLive.onclick = () => showTab("live");
  $tabSum.onclick  = () => showTab("summary");
  showTab("live");

  // ---- maximise / restore --------------------------------------------------
  // The default panel is small so it does not cover the meeting. Following a
  // conversation you cannot hear needs more room than glancing at it does, so this
  // toggles rather than picking one size for both jobs.
  let maximised = false;
  panel.querySelector("#mct-max").onclick = () => {
    maximised = !maximised;
    panel.classList.toggle("max", maximised);
    if (maximised) { panel.style.left = ""; panel.style.top = ""; }
    $log.scrollTop = $log.scrollHeight;
  };

  // ---- summary -------------------------------------------------------------
  panel.querySelector("#mct-sum-go").onclick = async () => {
    const who = $sumWho.value;
    const segments = bySpeaker.get(who) || [];
    if (!who || !segments.length) { $sumOut.textContent = "Nothing recorded for that speaker yet."; return; }
    if (!server.ok) { $sumOut.textContent = "Translator offline — start the local service."; return; }

    $sumOut.className = "mct-sum-out";
    $sumOut.textContent = `Summarising ${segments.length} segments from ${who}…`;
    try {
      const r = await fetch(`${SERVER}/summarize`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ speaker: who, segments }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      if (d.error) {
        $sumOut.className = "mct-sum-out err";
        $sumOut.textContent = d.error;
        return;
      }
      // Summaries are long-form prose; RTL matters even more here than for a
      // single caption line.
      $sumOut.className = "mct-sum-out" + (server.rtl ? " rtl" : "");
      $sumOut.textContent = d.summary;
      meta(`summarised ${d.segments} segments · ${Math.round(d.ms)}ms`);
    } catch (e) {
      $sumOut.className = "mct-sum-out err";
      $sumOut.textContent = `Could not reach the translator at ${SERVER}`;
    }
  };

  panel.querySelector("#mct-close").onclick = () => setPanelOpen(false, /*byUser=*/true);

  panel.querySelector("#mct-retry").onclick = () => {
    meta("reconnecting to translator…");
    loadConfig();
  };

  panel.querySelector("#mct-dump").onclick = () => {
    if (!state.container) { meta("not attached yet"); return; }
    const tree = (node, d = 0) => {
      if (d > 6) return "";
      const pad = "  ".repeat(d);
      const cls = (node.className || "").toString().slice(0, 60);
      const own = Array.from(node.childNodes)
        .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(" ").slice(0, 70);
      let s = `${pad}<${node.tagName.toLowerCase()}`
            + (node.id ? ` id="${node.id}"` : "")
            + (cls ? ` class="${cls}"` : "")
            + (node.getAttribute("data-tid") ? ` data-tid="${node.getAttribute("data-tid")}"` : "")
            + `>` + (own ? `  "${own}"` : "") + "\n";
      for (const c of node.children) s += tree(c, d + 1);
      return s;
    };
    const dump = tree(state.container);
    console.log("[caption] STRUCTURE ----\n" + dump);
    navigator.clipboard.writeText(dump).then(
      () => meta("structure copied to clipboard — paste it back"),
      () => meta("structure printed to console")
    );
  };

  panel.querySelector("#mct-copy").onclick = () => {
    const txt = transcript.map(r => `${r.t} ${r.speaker ? r.speaker + ": " : ""}${r.text}`).join("\n");
    navigator.clipboard.writeText(txt).then(() => meta(`copied ${transcript.length} segments`));
  };

  let hidden = false;
  panel.querySelector("#mct-hide").onclick = () => {
    hidden = !hidden;
    $log.style.display = hidden ? "none" : "";
    panel.querySelector("#mct-hide").textContent = hidden ? "+" : "–";
  };

  // Drag by the header — the panel will otherwise cover something important.
  (() => {
    const head = panel.querySelector("#mct-head");
    let sx, sy, ox, oy, dragging = false;
    head.addEventListener("mousedown", e => {
      if (e.target.tagName === "BUTTON") return;
      dragging = true;
      const r = panel.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
      panel.style.right = "auto"; panel.style.bottom = "auto";
      e.preventDefault();
    });
    document.addEventListener("mousemove", e => {
      if (!dragging) return;
      panel.style.left = ox + e.clientX - sx + "px";
      panel.style.top  = oy + e.clientY - sy + "px";
    });
    document.addEventListener("mouseup", () => { dragging = false; });
  })();

  loadConfig();
  // The companion is a separate process the user starts by hand; poll until it
  // appears rather than making them reload the page.
  setInterval(() => { if (!server.ok) loadConfig(); }, 15000);

  console.log(
    "[caption] loaded |", IS_TOP ? "TOP frame" : "iframe:" + location.href.slice(0, 80),
    "| helper: __mctFindByText('hello')"
  );
})();
