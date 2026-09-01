/*
 * A DOM small enough to run extension/content.js in a JS engine, and no smaller.
 *
 * Not a browser. It implements only what content.js touches, and it exists for
 * one reason: without it, the only way to find out whether the panel still
 * renders captions is to load it into Teams and speak. Two regressions reached a
 * real meeting that way. A fake caption fed to the real code catches them here.
 */

// ---------------------------------------------------------------- selectors

function parseSelector(sel) {
  // One compound step: tag, #id, .class, [attr], [attr="v"], [attr*="v"].
  const step = { tag: null, id: null, classes: [], attrs: [] };
  const re = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:([*^$]?=)"([^"]*)")?\]/g;
  let m;
  while ((m = re.exec(sel))) {
    if (m[1]) step.tag = m[1].toLowerCase();
    else if (m[2]) step.id = m[2];
    else if (m[3]) step.classes.push(m[3]);
    else if (m[4]) step.attrs.push({ name: m[4], op: m[5] || null, value: m[6] ?? null });
  }
  return step;
}

function matchesStep(node, step) {
  if (step.tag && node.tagName.toLowerCase() !== step.tag) return false;
  if (step.id && node.id !== step.id) return false;
  for (const c of step.classes) if (!node.classList.contains(c)) return false;
  for (const a of step.attrs) {
    const v = node.getAttribute(a.name);
    if (v == null) return false;
    if (a.op === "=" && v !== a.value) return false;
    if (a.op === "*=" && !v.includes(a.value)) return false;
    if (a.op === "^=" && !v.startsWith(a.value)) return false;
    if (a.op === "$=" && !v.endsWith(a.value)) return false;
  }
  return true;
}

/** Only the last step is checked against the node; ancestry is checked upward. */
function matchesSelector(node, sel) {
  for (const one of sel.split(",")) {
    const steps = one.trim().split(/\s+/).filter(Boolean).map(parseSelector);
    if (!steps.length) continue;
    if (!matchesStep(node, steps[steps.length - 1])) continue;
    let i = steps.length - 2, cur = node.parentElement;
    while (i >= 0 && cur) {
      if (matchesStep(cur, steps[i])) i--;
      cur = cur.parentElement;
    }
    if (i < 0) return true;
  }
  return false;
}

// ---------------------------------------------------------------- nodes

let mutationSinks = [];
let pendingMutations = false;

class Node {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attributes = {};
    this.style = {};
    this.dataset = new Proxy({}, {
      set: (t, k, v) => { t[k] = v; this.attributes["data-" + k] = String(v); return true; },
      get: (t, k) => t[k],
    });
    this._text = "";
    this._className = "";
    this._listeners = {};
    // Numbers the panel reads back; the harness sets them where a test cares.
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 100;
    this.offsetHeight = 20;
    this.classList = {
      add:    (c) => { const s = new Set(this._classes()); s.add(c); this._setClasses(s); },
      remove: (c) => { const s = new Set(this._classes()); s.delete(c); this._setClasses(s); },
      toggle: (c, on) => { on === undefined ? (this.classList.contains(c) ? this.classList.remove(c)
                                                                          : this.classList.add(c))
                                            : (on ? this.classList.add(c) : this.classList.remove(c)); },
      contains: (c) => this._classes().includes(c),
    };
  }
  _classes() { return this._className.split(/\s+/).filter(Boolean); }
  _setClasses(s) { this._className = [...s].join(" "); }

  get className() { return this._className; }
  set className(v) { this._className = String(v); }
  get id() { return this.attributes.id || ""; }
  set id(v) { this.attributes.id = String(v); }

  get textContent() {
    return this.children.length ? this.children.map(c => c.textContent).join("") : this._text;
  }
  set textContent(v) { this._text = String(v); this.children = []; this._notify(); }
  get innerText() { return this.textContent; }

  get firstChild() { return this.children[0] || null; }
  get parentNode() { return this.parentElement; }
  contains(n) { while (n) { if (n === this) return true; n = n.parentElement; } return false; }
  removeAttribute(k) { delete this.attributes[k]; }
  getRootNode() { let n = this; while (n.parentElement) n = n.parentElement;
                  return n === document.documentElement ? document : n; }
  get shadowRoot() { return null; }
  get isConnected() {
    let n = this; while (n.parentElement) n = n.parentElement;
    return n === document.documentElement || n === document;
  }

  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }

  appendChild(kid) {
    if (kid.parentElement) kid.parentElement.removeChild(kid);
    kid.parentElement = this;
    this.children.push(kid);
    this._notify();
    return kid;
  }
  removeChild(kid) {
    const i = this.children.indexOf(kid);
    if (i >= 0) { this.children.splice(i, 1); kid.parentElement = null; this._notify(); }
    return kid;
  }
  remove() { if (this.parentElement) this.parentElement.removeChild(this); }

  _notify() {
    // Queued, never called inline. A real MutationObserver runs its callback as a
    // microtask, after the mutation is finished. Calling it synchronously lets
    // scan() re-enter itself from inside render() and blow the stack -- an
    // artefact of the harness that would never happen in a browser.
    pendingMutations = true;
  }

  _walk(out = []) { for (const c of this.children) { out.push(c); c._walk(out); } return out; }

  querySelectorAll(sel) { return this._walk().filter(n => matchesSelector(n, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) {
    let n = this;
    while (n) { if (matchesSelector(n, sel)) return n; n = n.parentElement; }
    return null;
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 380, height: 300 }; }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  dispatch(type, ev = {}) { for (const fn of this._listeners[type] || []) fn(ev); }
}

// ---------------------------------------------------------------- document

const document = {
  documentElement: new Node("html"),
  createElement: (t) => new Node(t),
  _listeners: {},
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); },
  dispatch(type, ev = {}) { for (const fn of this._listeners[type] || []) fn(ev); },
  visibilityState: "visible",
};
document.head = new Node("head");
document.body = new Node("body");
document.documentElement.appendChild(document.head);
document.documentElement.appendChild(document.body);
document.querySelectorAll = (s) => document.documentElement.querySelectorAll(s);
document.querySelector  = (s) => document.documentElement.querySelector(s);

// ---------------------------------------------------------------- globals

const CSS = { escape: (s) => String(s).replace(/["\\]/g, "\\$&") };

class MutationObserver {
  constructor(fn) { this.fn = fn; }
  observe() { mutationSinks.push(this.fn); }
  disconnect() { mutationSinks = mutationSinks.filter(f => f !== this.fn); }
}

// Timers are driven by the test, not by wall time: a caption settles after a
// number of milliseconds, and a test should not have to wait them out.
let now = 0, timerId = 0;

/*
 * Date must follow the same clock.
 *
 * It did not, and that quietly hollowed out every time-based test here. The code
 * under test asks Date.now() how long a container has been silent, how long ago a
 * caption was first seen, when it last re-attached -- and against the real clock
 * all of those are a few milliseconds, because a test runs instantly. Rules with
 * multi-second thresholds could therefore never fire, and a test that expected
 * them not to fire passed for entirely the wrong reason.
 */
const _RealDate = globalThis.Date;
const _BASE = _RealDate.parse("2026-09-01T15:00:00Z");
class Date extends _RealDate {
  constructor(...a) { super(...(a.length ? a : [_BASE + now])); }
  static now() { return _BASE + now; }
}
const timers = new Map();
function setTimeout(fn, ms = 0) { timers.set(++timerId, { fn, at: now + ms, every: 0 }); return timerId; }
function setInterval(fn, ms = 0) { timers.set(++timerId, { fn, at: now + ms, every: ms }); return timerId; }
function clearTimeout(id) { timers.delete(id); }
function clearInterval(id) { timers.delete(id); }

/** Drain queued mutation callbacks, the way a browser drains microtasks. */
function flushMutations(limit = 20) {
  while (pendingMutations && limit-- > 0) {
    pendingMutations = false;
    for (const fn of mutationSinks.slice()) fn();
  }
}

function advance(ms) {
  const until = now + ms;
  flushMutations();
  for (;;) {
    let next = null;
    for (const [id, t] of timers) if (t.at <= until && (!next || t.at < next[1].at)) next = [id, t];
    if (!next) break;
    const [id, t] = next;
    now = t.at;
    if (t.every) t.at = now + t.every; else timers.delete(id);
    t.fn();
    flushMutations();
  }
  now = until;
}

// fetch is answered by the test through this table.
const routes = {};
const calls = [];
function fetch(url, opts = {}) {
  const path = String(url).replace(/^https?:\/\/[^/]+/, "");
  const body = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ path, body });
  const handler = routes[path];
  if (!handler) return Promise.reject(new Error("no route for " + path));
  const data = handler(body);
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
}

const navigator = { language: "en-US", languages: ["en-US"], clipboard: { writeText: () => Promise.resolve() } };
const localStorage = (() => { const m = {}; return {
  getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, }; })();
// hostname, not just href: the platform table matches on it, and leaving it
// undefined silently selected the generic fallback for every test.
const location = {
  href: "https://teams.cloud.microsoft/v2/",
  hostname: "teams.cloud.microsoft",
};
const innerWidth = 1600, innerHeight = 900;
function addEventListener() {}

const window = { top: null };
window.top = window;
const globalThis_chrome = undefined;   // no extension APIs: exercise the fallback

const logLines = [];
const console = {
  log:  (...a) => logLines.push(["log", a.map(String).join(" ")]),
  warn: (...a) => logLines.push(["warn", a.map(String).join(" ")]),
  error:(...a) => logLines.push(["error", a.map(String).join(" ")]),
};
