/*
 * Creates the hidden page that does the speaking, and passes messages to it.
 *
 * A content script cannot create an offscreen document itself, so this sits in
 * between. It is otherwise idle: no tabs are read, no requests are made, nothing
 * is stored here.
 */
"use strict";

const PAGE = "offscreen.html";

// createDocument throws if one already exists, and two messages arriving together
// would both see "none" and both try. One promise, shared.
let creating = null;

async function haveDocument() {
  try {
    if (chrome.offscreen.hasDocument) return await chrome.offscreen.hasDocument();
  } catch (e) { /* fall through to the older check */ }
  try {
    const url = chrome.runtime.getURL(PAGE);
    const all = await clients.matchAll();
    return all.some(c => c.url === url);
  } catch (e) {
    return false;
  }
}

async function ensureDocument() {
  if (await haveDocument()) return true;
  if (creating) { await creating; return true; }
  try {
    creating = chrome.offscreen.createDocument({
      url: PAGE,
      reasons: ["AUDIO_PLAYBACK"],
      justification:
        "Speak translated meeting captions from outside the meeting tab, so the "
        + "tab can be muted without silencing the translation.",
    });
    await creating;
    return true;
  } catch (e) {
    console.warn("[caption] could not open the speech page:", e && e.message);
    return false;
  } finally {
    creating = null;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  // Only messages addressed here. The offscreen page and this worker share a
  // message bus, so without this the worker would answer its own forwards.
  if (!msg || msg.target !== "background") return;

  (async () => {
    if (msg.type === "close") {
      if (await haveDocument()) await chrome.offscreen.closeDocument();
      respond({ ok: true });
      return;
    }
    const ready = await ensureDocument();
    if (!ready) { respond({ ok: false }); return; }
    chrome.runtime.sendMessage({ ...msg, target: "offscreen" }).catch(() => {});
    respond({ ok: true });
  })();

  return true;   // the reply is asynchronous
});
