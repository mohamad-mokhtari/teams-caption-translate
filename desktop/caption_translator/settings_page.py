"""
The settings page: a small web page served by the translator itself, opened from
the tray menu in the default browser.

A page rather than a native window: it needs no GUI toolkit inside the .exe, it
looks like the panel people already use, and it can be tested without a screen.

Anything on 127.0.0.1 can be reached by any website the person visits, so:
  - every call needs a random token minted when the app starts and known only to
    the tray menu (it is in the URL the tray opens);
  - requests from another origin, or addressed to another host name (DNS
    rebinding), are refused;
  - the saved API key never leaves the process -- the page only ever sees a
    masked form, and an empty key field means "keep the one I have".
"""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import APP_NAME, PORT, __version__, autostart, store


def mount(app: FastAPI, token: str) -> None:
    """Add the settings routes to the server app. Safe to call more than once."""
    app.state.settings_token = token
    if getattr(app.state, "settings_mounted", False):
        return
    app.state.settings_mounted = True
    app.include_router(router)


router = APIRouter()


def _refuse(req: Request) -> JSONResponse | None:
    port = req.url.port or PORT
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if req.headers.get("host", "") not in allowed_hosts:
        return JSONResponse({"error": "wrong host"}, status_code=403)
    origin = req.headers.get("origin")
    if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
        return JSONResponse({"error": "wrong origin"}, status_code=403)
    given = req.headers.get("x-mct-token") or req.query_params.get("token") or ""
    if not hmac.compare_digest(given, getattr(req.app.state, "settings_token", "") or "\0"):
        return JSONResponse({"error": "open the settings from the tray icon"}, status_code=403)
    return None


def _data() -> dict:
    from app import languages
    from app.config import settings
    from app.transcript import directory
    return {
        "values": store.current(),
        "languages": [{"code": l["code"], "name": l["name"], "native": l.get("native", "")}
                      for l in languages.LANGUAGES],
        "openai_models": [{"id": i, "label": t} for i, t in store.OPENAI_MODELS],
        "autostart": {"supported": autostart.supported(), "enabled": autostart.is_enabled()},
        "version": __version__,
        "settings_file": str(store.settings_path()),
        "transcript_dir_resolved": str(directory()),
        "provider_active": settings.provider,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(req: Request):
    bad = _refuse(req)
    if bad is not None:
        return HTMLResponse(_DENIED, status_code=bad.status_code)
    token = req.app.state.settings_token
    return HTMLResponse(_PAGE.replace("__TOKEN__", json.dumps(token))
                             .replace("__APP__", APP_NAME))


@router.get("/settings/data")
def settings_data(req: Request):
    return _refuse(req) or _data()


@router.post("/settings")
async def settings_save(req: Request):
    bad = _refuse(req)
    if bad is not None:
        return bad
    try:
        body = await req.json()
        values = store.clean(body.get("values", {}))
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    store.write(store.settings_path(), values)
    store.apply(values)
    if "autostart" in body and autostart.supported():
        try:
            autostart.set_enabled(bool(body["autostart"]))
        except OSError as e:
            return JSONResponse({"error": f"saved, but Start with Windows failed: {e}"}, status_code=500)
    return _data()


@router.post("/settings/test")
def settings_test(req: Request):
    """Translate one sentence with the saved settings: proof the key and model work."""
    bad = _refuse(req)
    if bad is not None:
        return bad
    from app import languages
    from app.config import settings
    from app.providers import ProviderError, call
    from app.translate import build_messages
    name = languages.name_of(settings.target_lang, settings.target_lang_name)
    system, user = build_messages("Hi everyone, can you all hear me? Let's start with the merge request.",
                                  [], name, "English")
    try:
        out, ms, used = call(system, user)
        return {"ok": True, "translation": out, "ms": round(ms), "provider": used,
                "model": settings.openai_model if used == "openai" else
                         settings.anthropic_model if used == "anthropic" else settings.ollama_model}
    except ProviderError as e:
        return {"ok": False, "error": str(e)}


_DENIED = """<!doctype html><meta charset="utf-8"><title>Caption Translator</title>
<body style="font:15px system-ui;background:#14161c;color:#e8e8ea;padding:40px">
<h2>Open the settings from the tray icon</h2>
<p>Right-click the Caption Translator icon next to the clock and choose <b>Settings</b>.
This page is locked to that link so other websites cannot change your settings.</p></body>"""

_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__APP__ — Settings</title>
<style>
  :root { --bg:#14161c; --card:#1b1e26; --line:#2a2f3a; --text:#e8e8ea; --muted:#9096a3;
          --dim:#6b7280; --accent:#34d399; --warn:#fbbf24; --err:#f87171; --field:#11131a; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.5 "Segoe UI", ui-sans-serif, system-ui, sans-serif; }
  main { max-width: 680px; margin: 0 auto; padding: 28px 16px 64px; }
  header { display:flex; align-items:center; gap:12px; margin-bottom: 22px; }
  header .logo { width:34px; height:34px; border-radius:9px; background:linear-gradient(135deg,#34d399,#6366f1); }
  header h1 { font-size: 20px; margin: 0; }
  header .ver { color: var(--dim); font-size: 12px; margin-left: auto; }
  section { background:var(--card); border:1px solid var(--line); border-radius:12px;
            padding: 16px 18px; margin-bottom: 14px; }
  section h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
               color: var(--muted); margin: 0 0 12px; }
  label { display:block; font-weight:600; margin: 12px 0 4px; }
  label:first-of-type { margin-top: 0; }
  .hint { color: var(--dim); font-size: 12.5px; margin-top: 4px; }
  input[type=text], input[type=password], select, textarea {
    width:100%; background:var(--field); color:var(--text); border:1px solid var(--line);
    border-radius:8px; padding:8px 10px; font:inherit; }
  textarea { min-height: 76px; resize: vertical; }
  input:focus, select:focus, textarea:focus { outline: 2px solid #6366f1; outline-offset: -1px; }
  .row { display:flex; gap: 10px; align-items: center; }
  .row > * { flex: 1; }
  .check { display:flex; gap:10px; align-items:flex-start; font-weight: 600; margin: 4px 0; }
  .check input { margin-top: 4px; }
  .check span small { display:block; font-weight: 400; color: var(--dim); }
  .bar { position: sticky; bottom: 0; display:flex; gap:10px; align-items:center;
         padding: 14px 0; background: linear-gradient(transparent, var(--bg) 30%); }
  button { font:inherit; font-weight:600; border-radius:8px; padding:9px 18px; cursor:pointer;
           border:1px solid var(--line); background:#232833; color:var(--text); }
  button.primary { background: var(--accent); color:#06281b; border-color: var(--accent); }
  button:disabled { opacity: .5; cursor: default; }
  #status { color: var(--muted); font-size: 13px; }
  #status.ok { color: var(--accent); } #status.err { color: var(--err); }
  #test-out { margin-top: 10px; padding: 10px 12px; border-radius: 8px; background: var(--field);
              border:1px solid var(--line); display:none; white-space: pre-wrap; }
  #test-out.ok { display:block; border-color: #1f6f53; }
  #test-out.err { display:block; border-color: #7f2d2d; color: var(--err); }
  #test-out .tr { font-size: 15px; }
  .rtl { direction: rtl; text-align: right; font-family: Tahoma, "Segoe UI", sans-serif; }
  .banner { background: rgba(251,191,36,.08); border:1px solid rgba(251,191,36,.35);
            color: var(--warn); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; display:none; }
  .prov { display:none; } .prov.on { display:block; }
  code { background: var(--field); padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
</style></head>
<body><main>
  <header><div class="logo"></div><h1>__APP__</h1><span class="ver" id="ver"></span></header>

  <div class="banner" id="setup">Add an API key below to start translating, then press <b>Save</b>
    and <b>Test</b>.</div>

  <section>
    <h2>Translation</h2>
    <label for="provider">Provider</label>
    <select id="provider">
      <option value="openai">OpenAI</option>
      <option value="anthropic">Anthropic (Claude)</option>
      <option value="ollama">Ollama — local, nothing leaves this computer</option>
    </select>

    <div class="prov" data-prov="openai">
      <label for="openai_api_key">OpenAI API key</label>
      <input type="password" id="openai_api_key" autocomplete="off" spellcheck="false">
      <div class="hint" id="openai_key_hint"></div>
      <label for="openai_model">Model</label>
      <select id="openai_model_pick"></select>
      <input type="text" id="openai_model" spellcheck="false" style="margin-top:6px;display:none"
             placeholder="model id, e.g. gpt-6-luna">
    </div>
    <div class="prov" data-prov="anthropic">
      <label for="anthropic_api_key">Anthropic API key</label>
      <input type="password" id="anthropic_api_key" autocomplete="off" spellcheck="false">
      <div class="hint" id="anthropic_key_hint"></div>
      <label for="anthropic_model">Model</label>
      <input type="text" id="anthropic_model" spellcheck="false">
    </div>
    <div class="prov" data-prov="ollama">
      <label for="ollama_model">Ollama model</label>
      <input type="text" id="ollama_model" spellcheck="false">
      <div class="hint">Needs Ollama running on this computer (<code>ollama serve</code>).</div>
    </div>

    <div class="row" style="margin-top:14px">
      <div style="flex:0 0 auto"><button id="test" type="button">Test</button></div>
      <div class="hint" style="margin:0">Translates one sentence with the saved settings.</div>
    </div>
    <div id="test-out"></div>
  </section>

  <section>
    <h2>Your language</h2>
    <label for="target_lang">Default language to translate into</label>
    <select id="target_lang"></select>
    <div class="hint">Each person can still pick their own under ⚙ in the panel.</div>
  </section>

  <section>
    <h2>Glossary</h2>
    <label for="glossary">Terms to keep in English</label>
    <textarea id="glossary" spellcheck="false"></textarea>
    <div class="hint">Comma-separated. Include the jargon and abbreviations people <i>say</i> —
      product names, AIE, RE, merge request… This is how the translator knows that
      “marriage request” in your meetings means a merge request.</div>
  </section>

  <section>
    <h2>Transcripts</h2>
    <div class="check"><input type="checkbox" id="transcript_enabled">
      <span>Save each meeting to a file<small>Meeting content in clear text, on this computer.</small></span></div>
    <label for="transcript_dir">Folder</label>
    <input type="text" id="transcript_dir" spellcheck="false">
    <div class="hint" id="tdir"></div>
  </section>

  <section id="startup">
    <h2>Startup</h2>
    <div class="check"><input type="checkbox" id="autostart">
      <span>Start with Windows<small>Runs in the tray when you sign in, so the panel always has a translator.</small></span></div>
  </section>

  <div class="hint" id="file"></div>
  <div class="bar"><button class="primary" id="save" type="button">Save</button><span id="status"></span></div>
</main>
<script>
const TOKEN = __TOKEN__;
const H = { "content-type": "application/json", "x-mct-token": TOKEN };
const $ = (id) => document.getElementById(id);
const RTL = new Set(["fa","ar","he","ur","ps","yi","dv","ckb"]);
let data = null;

function opt(sel, value, text) { const o = document.createElement("option"); o.value = value; o.textContent = text; sel.appendChild(o); return o; }

function showProvider() {
  for (const d of document.querySelectorAll(".prov")) d.classList.toggle("on", d.dataset.prov === $("provider").value);
}

function fill(d) {
  data = d;
  const v = d.values;
  $("ver").textContent = "version " + d.version;
  $("provider").value = v.provider; showProvider();

  for (const [p, has] of [["openai", v.has_openai_key], ["anthropic", v.has_anthropic_key]]) {
    $(p + "_api_key").value = "";
    $(p + "_api_key").placeholder = has ? "saved (" + v[p + "_api_key"] + ") — leave empty to keep it" : "paste your key";
    $(p + "_key_hint").textContent = has ? "A key is saved. It is never shown in full." : "No key saved yet.";
  }

  const pick = $("openai_model_pick"); pick.textContent = "";
  for (const m of d.openai_models) opt(pick, m.id, m.label);
  opt(pick, "__other", "Other…");
  const known = d.openai_models.some(m => m.id === v.openai_model);
  pick.value = known ? v.openai_model : "__other";
  $("openai_model").value = v.openai_model;
  $("openai_model").style.display = known ? "none" : "block";

  $("anthropic_model").value = v.anthropic_model;
  $("ollama_model").value = v.ollama_model;

  const lang = $("target_lang"); lang.textContent = "";
  for (const l of d.languages) opt(lang, l.code, l.native && l.native !== l.name ? `${l.name} — ${l.native}` : l.name);
  lang.value = v.target_lang;

  $("glossary").value = v.glossary;
  $("transcript_enabled").checked = !!v.transcript_enabled;
  $("transcript_dir").value = v.transcript_dir;
  $("tdir").textContent = "Currently: " + d.transcript_dir_resolved;

  $("startup").style.display = d.autostart.supported ? "" : "none";
  $("autostart").checked = d.autostart.enabled;
  $("file").textContent = "Settings file: " + d.settings_file;

  const p = v.provider;
  $("setup").style.display = (p === "openai" && !v.has_openai_key) || (p === "anthropic" && !v.has_anthropic_key) ? "block" : "none";
}

function collect() {
  const pick = $("openai_model_pick").value;
  return {
    values: {
      provider: $("provider").value,
      openai_api_key: $("openai_api_key").value.trim(),
      openai_model: pick === "__other" ? $("openai_model").value.trim() : pick,
      anthropic_api_key: $("anthropic_api_key").value.trim(),
      anthropic_model: $("anthropic_model").value.trim(),
      ollama_model: $("ollama_model").value.trim(),
      target_lang: $("target_lang").value,
      glossary: $("glossary").value.replace(/\s*\n\s*/g, ",").replace(/,+/g, ",").replace(/^,|,$/g, ""),
      transcript_enabled: $("transcript_enabled").checked,
      transcript_dir: $("transcript_dir").value.trim(),
    },
    autostart: $("autostart").checked,
  };
}

function status(msg, cls = "") { const s = $("status"); s.textContent = msg; s.className = cls; }

async function load() {
  const r = await fetch("/settings/data", { headers: H });
  if (!r.ok) { status("Could not load settings — open this page from the tray icon.", "err"); return; }
  fill(await r.json());
}

$("provider").onchange = showProvider;
$("openai_model_pick").onchange = () => {
  const other = $("openai_model_pick").value === "__other";
  $("openai_model").style.display = other ? "block" : "none";
  if (other) $("openai_model").focus();
};

$("save").onclick = async () => {
  $("save").disabled = true; status("Saving…");
  try {
    const r = await fetch("/settings", { method: "POST", headers: H, body: JSON.stringify(collect()) });
    const d = await r.json();
    if (!r.ok) { status(d.error || "Could not save", "err"); return; }
    fill(d); status("Saved — used from the next caption. No restart needed.", "ok");
  } catch (e) { status("Could not reach the translator. Is it still running?", "err"); }
  finally { $("save").disabled = false; }
};

$("test").onclick = async () => {
  const out = $("test-out"); $("test").disabled = true;
  out.className = ""; out.textContent = "";
  status("Testing with the saved settings…");
  try {
    const r = await fetch("/settings/test", { method: "POST", headers: H });
    const d = await r.json();
    out.textContent = "";
    if (d.ok) {
      out.className = "ok";
      const tr = document.createElement("div"); tr.className = "tr" + (RTL.has(data.values.target_lang.split("-")[0]) ? " rtl" : "");
      tr.textContent = d.translation;
      const meta = document.createElement("div"); meta.className = "hint";
      meta.textContent = `${d.provider} · ${d.model} · ${d.ms} ms`;
      out.append(tr, meta); status("It works.", "ok");
    } else { out.className = "err"; out.textContent = d.error || "Failed"; status(""); }
  } catch (e) { out.className = "err"; out.textContent = "Could not reach the translator."; status(""); }
  finally { $("test").disabled = false; }
};

load();
</script>
</body></html>
"""
