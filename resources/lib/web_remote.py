# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import xbmc

from .database import FavouriteEntry, FavouritesEngine

# Logo embedded in the web header. Read from the PNG (a text-free crop of the addon icon)
# and inlined as a data URI, so no extra endpoint is needed to serve it.
_LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'media', 'ff_logo.png')
try:
    with open(_LOGO_PATH, 'rb') as _f:
        _LOGO_B64 = base64.b64encode(_f.read()).decode()
except OSError:
    _LOGO_B64 = ''

# --- Favourite thumbnails -------------------------------------------------
# Kodi stores a favourite's thumb as image://<urlencoded>/, a direct http(s) URL,
# a special:// path, an absolute local path, or empty. We serve it by index so the
# client never supplies a filesystem path (no arbitrary file reads).

_IMG_MIME = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
    '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
}

# special:// locations safe to expose: read-only, public addon/skin artwork.
# Never userdata/profile/temp/thumbnails (private data or untrusted caches).
_SAFE_SPECIAL_PREFIXES = (
    'special://home/addons/', 'special://xbmc/addons/',
    'special://home/media/', 'special://xbmc/media/',
)


def _resolve_special_prefixes():
    # xbmcvfs.translatePath() fails silently in the server's worker threads, so we resolve
    # special:// -> real path ONCE here, at import time (the service's main thread).
    try:
        import xbmcvfs
        translate = xbmcvfs.translatePath
    except (ImportError, AttributeError):
        translate = xbmc.translatePath
    mapping = {}
    for vpath in _SAFE_SPECIAL_PREFIXES:
        try:
            real = translate(vpath)
        except Exception:
            continue
        if real:
            mapping[vpath] = os.path.normpath(real)
    return mapping


_SPECIAL_PREFIX_MAP = _resolve_special_prefixes()
_ADDONS_DIRS = tuple(
    _SPECIAL_PREFIX_MAP[p] for p in ('special://home/addons/', 'special://xbmc/addons/')
    if p in _SPECIAL_PREFIX_MAP
)


def _decode_image_url(u):
    inner = u[len('image://'):]
    if inner.endswith('/'):
        inner = inner[:-1]
    return urllib.parse.unquote(inner) if inner else ''


def _special_to_path(vpath):
    if '..' in vpath:
        return None
    for prefix, base in _SPECIAL_PREFIX_MAP.items():
        if vpath.startswith(prefix):
            return os.path.join(base, vpath[len(prefix):].replace('/', os.sep))
    return None


def _is_safe_local_image(path):
    # Absolute path to an image under the Kodi addons dirs; realpath blocks traversal.
    if not os.path.isabs(path) or os.path.splitext(path)[1].lower() not in _IMG_MIME:
        return False
    try:
        real = os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return False
    return any(real == os.path.normcase(b) or real.startswith(os.path.normcase(b) + os.sep)
               for b in _ADDONS_DIRS)

_lock = threading.Lock()
_server = None
_server_thread = None
_port = None

WIN_PROP = 'flowfavmanager.web_remote.port'


def _try_bind(start):
    """Create the server on the first free port starting at `start`.

    Instead of pre-checking with socket.bind + close, we create the server directly,
    avoiding the TOCTOU window between the check and the real bind. ThreadingHTTPServer
    handles each request in its own (daemon) thread so thumbnail loads run in parallel and
    do not block the API or mutations; writes are already serialized by `_lock`.
    Returns (server, port), or (None, None) if every port is busy.
    """
    for p in range(start, start + 20):
        try:
            return ThreadingHTTPServer(('', p), _Handler), p
        except OSError:
            continue
    return None, None


def _fingerprint(entries):
    raw = '|'.join(f'{e.name}\x00{e.url}' for e in entries)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            body = _HTML_BYTES
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        elif path == '/api/favs':
            self._api_list()
        elif path == '/api/thumb':
            self._api_thumb()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n) or b'{}')
        except (ValueError, json.JSONDecodeError):
            self._json({'error': 'bad_request'}, 400)
            return

        path = self.path.split('?')[0]
        if path == '/api/favs/delete':
            self._do_delete(body)
        elif path == '/api/favs/move':
            self._do_move(body)
        elif path == '/api/favs/rename':
            self._do_rename(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _api_list(self):
        engine = FavouritesEngine()
        engine.load()
        entries = engine.entries
        fp = _fingerprint(entries)
        self._json({
            'favs': [{'i': i, 'title': e.name, 'thumb': bool(e.thumb)}
                     for i, e in enumerate(entries)],
            'fp': fp,
        })

    def _api_thumb(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            idx = int(qs.get('i', [''])[0])
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        engine = FavouritesEngine()
        engine.load()
        entries = engine.entries
        if not (0 <= idx < len(entries)):
            self._not_found()
            return
        self._serve_thumb((entries[idx].thumb or '').strip())

    def _serve_thumb(self, thumb):
        if thumb.startswith('image://'):
            thumb = _decode_image_url(thumb)
        # Some addons append options as "url|User-Agent=...". Drop them for http(s).
        if '|' in thumb and thumb.startswith(('http://', 'https://')):
            thumb = thumb.split('|', 1)[0].strip()

        if not thumb:
            self._not_found()
        elif thumb.startswith(('http://', 'https://')):
            self._serve_thumb_http(thumb)
        elif thumb.startswith('special://'):
            path = _special_to_path(thumb)
            if path and os.path.splitext(path)[1].lower() in _IMG_MIME:
                self._serve_thumb_file(path)
            else:
                self._not_found()
        elif os.path.isabs(thumb) and _is_safe_local_image(thumb):
            self._serve_thumb_file(thumb)
        else:
            self._not_found()

    def _serve_thumb_file(self, path):
        ct = _IMG_MIME.get(os.path.splitext(path)[1].lower())
        if not ct:
            self._not_found()
            return
        try:
            with open(path, 'rb') as f:
                body = f.read()
        except OSError:
            self._not_found()
            return
        self._send_image(body, ct)

    def _serve_thumb_http(self, url):
        # Proxy the remote image. System proxy off (a configured Windows proxy returns a
        # tunnel 404 for these public CDNs) and cert checks relaxed: it is only an image,
        # validated as image/* and size-capped.
        import ssl
        import urllib.error
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
        )
        # Re-encode path/query so spaces and non-ASCII don't raise on Request(). unquote first
        # keeps it idempotent: URLs that already arrived percent-encoded aren't double-encoded.
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/")
        query = urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&")
        safe_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
        # Some CDNs (e.g. Wikimedia) reject the default urllib User-Agent with 403.
        req = urllib.request.Request(safe_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with opener.open(req, timeout=4) as resp:
                ct = resp.headers.get('Content-Type', 'image/jpeg')
                if not ct.startswith('image/'):
                    self._not_found()
                    return
                body = resp.read(8 * 1024 * 1024 + 1)  # cap 8 MB; real thumbs are < 2 MB
            if len(body) > 8 * 1024 * 1024:
                self._not_found()
                return
        except (urllib.error.URLError, OSError, ValueError):
            self._not_found()
            return
        self._send_image(body, ct)

    def _send_image(self, body, ct):
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self):
        # no-store so a thumb that becomes available later is retried, not cached as 404.
        self.send_response(404)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def _load_verified(self, fp):
        """Re-read the file and check the fingerprint. Call INSIDE the `with _lock` block."""
        engine = FavouritesEngine()
        engine.load()
        entries = engine.entries
        if _fingerprint(entries) != fp:
            return None, None
        return engine, entries

    def _do_delete(self, body):
        idx = body.get('i')
        fp = body.get('fp')
        err = None
        with _lock:
            engine, entries = self._load_verified(fp)
            if engine is None:
                err = ({'error': 'conflict'}, 409)
            elif not (isinstance(idx, int) and not isinstance(idx, bool)
                      and 0 <= idx < len(entries)):
                err = ({'error': 'range'}, 400)
            else:
                entries.pop(idx)
                if not engine.save():
                    err = ({'error': 'save_failed'}, 500)
        # Network I/O outside the lock; it must not block other threads while writing to the socket
        self._json(*err) if err else self._json({'ok': True})

    def _do_move(self, body):
        idx = body.get('i')
        to = body.get('to')
        fp = body.get('fp')
        err = None
        with _lock:
            engine, entries = self._load_verified(fp)
            if engine is None:
                err = ({'error': 'conflict'}, 409)
            elif not (isinstance(idx, int) and not isinstance(idx, bool)
                      and isinstance(to, int) and not isinstance(to, bool)
                      and 0 <= idx < len(entries) and 0 <= to < len(entries)):
                err = ({'error': 'range'}, 400)
            else:
                item = entries.pop(idx)
                entries.insert(to, item)
                if not engine.save():
                    err = ({'error': 'save_failed'}, 500)
        self._json(*err) if err else self._json({'ok': True})

    def _do_rename(self, body):
        idx = body.get('i')
        fp = body.get('fp')
        title = (body.get('title') or '').strip()
        if not title:
            self._json({'error': 'empty'}, 400)
            return
        err = None
        with _lock:
            engine, entries = self._load_verified(fp)
            if engine is None:
                err = ({'error': 'conflict'}, 409)
            elif not (isinstance(idx, int) and not isinstance(idx, bool)
                      and 0 <= idx < len(entries)):
                err = ({'error': 'range'}, 400)
            else:
                old = entries[idx]
                if '▬' in old.name:
                    # Renaming a separator: keep its separator nature (the '▬' marker that
                    # _is_separator looks for) and its original colour, instead of turning it
                    # into a broken plain favourite.
                    m = re.search(r'\[COLOR (\w+)\]', old.name)
                    color = m.group(1) if m else 'gold'
                    title = f"[COLOR {color}][B]{title.upper()}[/B] {'▬' * 18}[/COLOR]"
                entries[idx] = FavouriteEntry(title, old.thumb, old.url)
                if not engine.save():
                    err = ({'error': 'save_failed'}, 500)
        self._json(*err) if err else self._json({'ok': True})


def start(port_start):
    global _server, _server_thread, _port
    if _server is not None:
        return _port
    srv, p = _try_bind(port_start)
    if srv is None:
        return None
    _server = srv
    _port = p
    _server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    _server_thread.start()
    xbmc.log(f'[FlowFavManager] Web remote started on port {p}', xbmc.LOGINFO)
    return p


def stop():
    global _server, _server_thread, _port
    if _server is None:
        return
    try:
        _server.shutdown()
        _server.server_close()
        _server_thread.join(timeout=3)
    except Exception as e:
        # Best-effort resource cleanup: a failure shutting the server down must not propagate.
        xbmc.log(f'[FlowFavManager] Web remote stop: {e}', xbmc.LOGWARNING)
    _server = None
    _server_thread = None
    _port = None
    xbmc.log('[FlowFavManager] Web remote stopped', xbmc.LOGINFO)


def is_running():
    return _server is not None


def get_port():
    return _port


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>Flow FavManager</title>
<style>
:root{--bg:#0d1117;--fg:#c9d1d9;--muted:#8b949e;--accent:#58a6ff;--card-bg:#161b22;
      --card-bd:#30363d;--sec:#21262d;--row:#1c2128;--err:#f85149;--dot:#3fb950}
@media (prefers-color-scheme:light){
  :root{--bg:#f6f8fa;--fg:#1f2328;--muted:#6e7781;--accent:#0969da;--card-bg:#fff;
        --card-bd:#d1d9e0;--sec:#eaeef2;--row:#f6f8fa;--err:#cf222e;--dot:#1a7f37}
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:var(--bg);color:var(--fg);min-height:100vh;min-height:100dvh;
     padding:16px 16px calc(20px + env(safe-area-inset-bottom)) 16px}
.app{max-width:560px;margin:0 auto}
.topbar{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.logo{width:34px;height:34px;border-radius:8px;flex-shrink:0;display:block;
      box-shadow:0 1px 4px rgba(0,0,0,.3)}
.grow{flex:1}
.dot{width:9px;height:9px;border-radius:50%;background:var(--dot);flex-shrink:0;
     box-shadow:0 0 0 3px color-mix(in srgb,var(--dot) 22%,transparent)}
.dot.off{background:var(--err);box-shadow:0 0 0 3px color-mix(in srgb,var(--err) 22%,transparent)}
.count{background:var(--sec);border:1px solid var(--card-bd);border-radius:999px;
       padding:3px 11px;font-size:.8em;font-weight:700}
.iconbtn{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;
         border:1px solid var(--card-bd);background:var(--card-bg);color:var(--muted);
         border-radius:8px;cursor:pointer;transition:color .15s,border-color .15s,transform .1s}
.iconbtn:hover{color:var(--fg);border-color:var(--accent)}
.iconbtn:active{transform:scale(.94)}
.iconbtn svg{width:18px;height:18px}
.card{background:var(--card-bg);border:1px solid var(--card-bd);border-radius:12px;padding:16px}
.card h2{font-size:.8em;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);
         font-weight:700;margin-bottom:14px}
.list{list-style:none;display:flex;flex-direction:column;gap:6px}
.item{display:flex;align-items:center;gap:12px;padding:10px;background:var(--row);
      border:1px solid var(--card-bd);border-radius:8px}
.avatar{width:36px;height:36px;border-radius:6px;background:var(--sec);color:var(--accent);
        display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.05em;
        flex-shrink:0;user-select:none;overflow:hidden;background-size:cover;background-position:center}
.avatar.has-img{color:transparent}
.title{flex:1;min-width:0;font-size:.92em;font-weight:600;white-space:nowrap;
       overflow:hidden;text-overflow:ellipsis}
.acts{display:flex;gap:4px;flex-shrink:0}
.act{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;
     border:1px solid var(--card-bd);border-radius:7px;background:var(--sec);color:var(--fg);
     cursor:pointer;font-size:16px;line-height:1;
     transition:background .15s,color .15s,border-color .15s,transform .1s}
.act svg{width:18px;height:18px}
.act:hover:not(:disabled){background:var(--card-bd);border-color:var(--accent);color:var(--accent)}
.act:active:not(:disabled){transform:scale(.9)}
.act:disabled{opacity:.25;cursor:default}
.act.danger:hover:not(:disabled){background:var(--err);border-color:var(--err);color:#fff}
.empty{text-align:center;color:var(--muted);padding:40px 16px;font-size:.9em}
.toast{position:fixed;left:50%;bottom:calc(20px + env(safe-area-inset-bottom));
       transform:translateX(-50%) translateY(1.5rem);background:var(--card-bg);
       border:1px solid var(--card-bd);color:var(--fg);padding:10px 16px;border-radius:8px;
       font-size:.85em;opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;
       z-index:100;box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:90vw}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.modal-back{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;
            justify-content:center;padding:20px;z-index:200}
.modal-box{background:var(--card-bg);border:1px solid var(--card-bd);border-radius:12px;
           padding:20px;width:100%;max-width:360px;box-shadow:0 8px 32px rgba(0,0,0,.4)}
.modal-box h3{font-size:1.05em;margin-bottom:12px}
.modal-msg{color:var(--muted);font-size:.9em;margin-bottom:16px;word-break:break-word;line-height:1.4}
.modal-box input{width:100%;background:var(--bg);border:1px solid var(--card-bd);color:var(--fg);
                 border-radius:8px;padding:10px 12px;font:inherit;font-size:.95em;outline:none;
                 margin-bottom:16px}
.modal-box input:focus{border-color:var(--accent)}
.modal-row{display:flex;gap:8px;justify-content:flex-end}
.mbtn{border:0;border-radius:8px;padding:10px 16px;font:inherit;font-weight:600;cursor:pointer;
      font-size:.9em;transition:transform .1s,filter .15s}
.mbtn:active{transform:scale(.96)}
.mbtn.ghost{background:var(--sec);color:var(--fg)}
.mbtn.primary{background:var(--accent);color:#fff}
.mbtn.danger{background:var(--err);color:#fff}
.mbtn:hover{filter:brightness(1.1)}
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <img class="logo" src="data:image/png;base64,__LOGO__" alt="Flow FavManager">
    <span class="grow"></span>
    <span class="count" id="count">0</span>
    <span class="dot" id="dot" title="Connecting\\u2026"></span>
    <button class="iconbtn" id="reload" title="Refresh"></button>
  </div>
  <div class="card">
    <h2>Kodi Favourites</h2>
    <ul class="list" id="list"></ul>
    <p class="empty" id="empty" style="display:none">No favourites.</p>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
const _NS='xmlns="http://www.w3.org/2000/svg"';
const _A='fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
const ICON={
  up:`<svg ${_NS} viewBox="0 0 24 24" ${_A}><path d="M12 19V6"/><path d="M5 13l7-7 7 7"/></svg>`,
  down:`<svg ${_NS} viewBox="0 0 24 24" ${_A}><path d="M12 5v13"/><path d="M19 11l-7 7-7-7"/></svg>`,
  edit:`<svg ${_NS} viewBox="0 0 24 24" ${_A}><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>`,
  del:`<svg ${_NS} viewBox="0 0 24 24" ${_A}><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>`,
  reload:`<svg ${_NS} viewBox="0 0 24 24" ${_A}><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/></svg>`,
};

// Text fallback in case the SVG doesn't render: ensures the button always shows something.
const GLYPH={up:'\\u2191',down:'\\u2193',edit:'\\u270e',del:'\\u2715',reload:'\\u21bb'};

// SVG via DOMParser (image/svg+xml): builds the nodes in the correct SVG namespace without going
// through the HTML parser, which in some browsers won't render SVG injected via innerHTML.
function svg(str){
  try{
    const doc=new DOMParser().parseFromString(str,'image/svg+xml');
    const el=doc.documentElement;
    if(el && el.localName==='svg' && !el.getElementsByTagName('parsererror').length)
      return document.importNode(el,true);
  }catch(e){}
  return null;
}

// Sets the icon on a button: SVG if possible, text glyph otherwise.
function setIcon(el,key){
  const node=svg(ICON[key]);
  if(node) el.appendChild(node); else el.textContent=GLYPH[key];
}

const BB = /\\[[^\\]]*\\]/g;
const $ = id => document.getElementById(id);
const plain = s => (s||'').replace(BB,'').trim();
const avatar = s => { const t=plain(s); return t ? [...t][0].toUpperCase() : '\\u2605'; };

let _fp='', _toastT=null;

function toast(msg,ms=2500){
  const el=$('toast'); el.textContent=msg; el.classList.add('show');
  clearTimeout(_toastT); _toastT=setTimeout(()=>el.classList.remove('show'),ms);
}

function modal({title,message,input,okText,danger}){
  return new Promise(resolve=>{
    const back=document.createElement('div'); back.className='modal-back';
    const box=document.createElement('div'); box.className='modal-box';
    const h=document.createElement('h3'); h.textContent=title; box.appendChild(h);
    if(message){ const p=document.createElement('p'); p.className='modal-msg';
                 p.textContent=message; box.appendChild(p); }
    let inp=null;
    if(input!==undefined){
      inp=document.createElement('input'); inp.type='text'; inp.value=input;
      box.appendChild(inp);
    }
    const row=document.createElement('div'); row.className='modal-row';
    const cancel=document.createElement('button'); cancel.className='mbtn ghost';
    cancel.textContent='Cancel';
    const ok=document.createElement('button');
    ok.className='mbtn '+(danger?'danger':'primary'); ok.textContent=okText||'OK';
    row.append(cancel,ok); box.appendChild(row); back.appendChild(box);
    document.body.appendChild(back);
    const close=v=>{ back.remove(); document.removeEventListener('keydown',onKey); resolve(v); };
    const accept=()=>{ if(inp){ const v=inp.value.trim(); if(!v) return; close(v); } else close(true); };
    function onKey(e){ if(e.key==='Escape') close(null); else if(e.key==='Enter') accept(); }
    cancel.onclick=()=>close(null);
    ok.onclick=accept;
    back.onclick=e=>{ if(e.target===back) close(null); };
    document.addEventListener('keydown',onKey);
    if(inp){ inp.focus(); inp.select(); }
  });
}

async function api(method,path,body){
  const opts={method,headers:{'Content-Type':'application/json'}};
  if(body!==undefined) opts.body=JSON.stringify(body);
  const r=await fetch(path,opts);
  const d=await r.json().catch(()=>({}));
  if(!r.ok) throw Object.assign(new Error(d.error||r.statusText),{status:r.status});
  return d;
}

async function load(showToast=false){
  try{
    const d=await api('GET','/api/favs');
    _fp=d.fp; render(d.favs);
    const dot=$('dot'); dot.classList.remove('off'); dot.title='Connected';
    if(showToast) toast('List refreshed');
  }catch{
    const dot=$('dot'); dot.classList.add('off'); dot.title='Offline';
  }
}

function render(favs){
  const ul=$('list'), empty=$('empty');
  $('count').textContent=favs.length;
  ul.innerHTML='';
  if(!favs.length){ empty.style.display=''; return; }
  empty.style.display='none';
  favs.forEach((fav,i)=>{
    const li=document.createElement('li'); li.className='item';
    const av=document.createElement('div'); av.className='avatar'; av.textContent=avatar(fav.title);
    if(fav.thumb){
      // Loads the actual thumbnail; if it arrives, replaces the initial letter. If it fails (404), keep the initial.
      const url=`/api/thumb?i=${i}&v=${encodeURIComponent(_fp)}`;
      const probe=new Image();
      probe.onload=()=>{ av.style.backgroundImage=`url("${url}")`; av.textContent=''; av.classList.add('has-img'); };
      probe.src=url;
    }
    const sp=document.createElement('span'); sp.className='title';
    sp.textContent=plain(fav.title); sp.title=plain(fav.title);
    const ac=document.createElement('div'); ac.className='acts';
    ac.append(
      btn('up',  'Move up',   i===0,             false, ()=>doMove(i,i-1)),
      btn('down','Move down', i===favs.length-1, false, ()=>doMove(i,i+1)),
      btn('edit','Rename',    false,             false, ()=>doRename(i,fav.title)),
      btn('del', 'Delete',    false,             true,  ()=>doDelete(i,fav.title))
    );
    li.append(av,sp,ac); ul.appendChild(li);
  });
}

function btn(key,title,disabled,isDanger,fn){
  const b=document.createElement('button');
  b.className='act'+(isDanger?' danger':''); setIcon(b,key);
  b.title=title; b.setAttribute('aria-label',title); b.disabled=disabled; b.onclick=fn;
  return b;
}

async function doMove(i,to){
  try{ await api('POST','/api/favs/move',{i,to,fp:_fp}); await load(); }
  catch(e){ onErr(e,'Could not move'); }
}

async function doDelete(i,title){
  const ok=await modal({title:'Delete favourite',
    message:'Delete "'+plain(title)+'"?',
    okText:'Delete',danger:true});
  if(!ok) return;
  try{ await api('POST','/api/favs/delete',{i,fp:_fp}); await load(); toast('Deleted'); }
  catch(e){ onErr(e,'Could not delete'); }
}

async function doRename(i,current){
  const newName=await modal({title:'Rename favourite',input:plain(current),okText:'Save'});
  if(newName===null) return;
  try{ await api('POST','/api/favs/rename',{i,fp:_fp,title:newName}); await load(); toast('Renamed'); }
  catch(e){ onErr(e,'Could not rename'); }
}

function onErr(e,fallback){
  if(e.status===409){ toast('The list changed, reloading\\u2026'); load(); }
  else toast(fallback);
}

setIcon($('reload'),'reload');
$('reload').onclick=()=>load(true);
load();
setInterval(load,10000);
</script>
</body>
</html>
"""

# Pre-encoded at import time; avoids re-encoding on every GET /
_HTML_BYTES = _HTML.replace('__LOGO__', _LOGO_B64).encode('utf-8')
