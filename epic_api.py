"""HTTP control surface for the EPIC viewer.

A stdlib http.server (no third-party deps) runs in a daemon thread and talks to
the pygame main loop through ApiBridge: a command deque in, a status dict out.
All AppState mutation stays on the main thread — this module only routes
requests and hands commands across the thread boundary."""

import collections
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EPIC Viewer</title>
<style>
 body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:1.2rem;max-width:520px;margin:auto}
 h1{font-size:1.2rem;font-weight:600}
 section{margin:1rem 0;padding:1rem;background:#1c1c1c;border-radius:12px}
 h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:#999;margin:0 0 .6rem}
 button{font-size:1rem;padding:.7rem 1.1rem;margin:.25rem;border:0;border-radius:10px;background:#2d6cdf;color:#fff}
 button.alt{background:#444}
 #status{font-size:.85rem;color:#bbb;white-space:pre-line;line-height:1.5}
</style></head><body>
<h1>DSCOVR:EPIC Viewer</h1>
<section><h2>Weather overlay</h2>
 <button onclick="post('/api/overlay/show')">Show</button>
 <button class="alt" onclick="post('/api/overlay/hide')">Hide</button></section>
<section><h2>Screen</h2>
 <button onclick="post('/api/screen/on')">On</button>
 <button class="alt" onclick="post('/api/screen/off')">Off</button>
 <button class="alt" onclick="post('/api/screen/auto')">Auto (schedule)</button></section>
<section><h2>Image</h2>
 <button onclick="post('/api/image/refresh')">Load latest EPIC image</button></section>
<section><h2>Status</h2><div id="status">…</div></section>
<script>
 async function post(p){await fetch(p,{method:'POST'});setTimeout(refresh,400);}
 async function refresh(){
  try{const r=await fetch('/api/status');const s=await r.json();
   const w=s.weather?`${s.weather.temp_c}°C ${s.weather.condition}`:'—';
   document.getElementById('status').textContent=
    `screen: ${s.screen_on?'ON':'OFF'} (${s.screen_override})\\nmode: ${s.mode}\\nweather: ${w}\\nlast image: ${s.last_image_date||'—'}`;
  }catch(e){document.getElementById('status').textContent='offline';}
 }
 refresh();setInterval(refresh,5000);
</script></body></html>"""

_POST_ROUTES = {
    '/api/overlay/show': {'cmd': 'overlay', 'action': 'show'},
    '/api/overlay/hide': {'cmd': 'overlay', 'action': 'hide'},
    '/api/overlay/toggle': {'cmd': 'overlay', 'action': 'toggle'},
    '/api/screen/on': {'cmd': 'screen', 'action': 'on'},
    '/api/screen/off': {'cmd': 'screen', 'action': 'off'},
    '/api/screen/auto': {'cmd': 'screen', 'action': 'auto'},
    '/api/image/refresh': {'cmd': 'refresh_image'},
}


class ApiBridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._commands = collections.deque()
        self._status = {}

    def push_command(self, cmd):
        with self._lock:
            self._commands.append(cmd)

    def drain_commands(self):
        with self._lock:
            drained = list(self._commands)
            self._commands.clear()
        return drained

    def set_status(self, status):
        with self._lock:
            self._status = dict(status)

    def get_status(self):
        with self._lock:
            return dict(self._status)


def dispatch(method, path, status_provider):
    """Pure router. status_provider is a zero-arg callable returning the status
    dict. Returns (code, content_type, body_bytes, command_or_None)."""
    if method == 'GET' and path == '/':
        return 200, 'text/html; charset=utf-8', DASHBOARD_HTML.encode('utf-8'), None
    if method == 'GET' and path == '/api/status':
        return 200, 'application/json', json.dumps(status_provider()).encode('utf-8'), None
    if method == 'POST' and path in _POST_ROUTES:
        cmd = _POST_ROUTES[path]
        return 200, 'application/json', json.dumps({'ok': True, 'command': cmd}).encode('utf-8'), cmd
    return 404, 'application/json', json.dumps({'ok': False, 'error': 'not found'}).encode('utf-8'), None


class _Handler(BaseHTTPRequestHandler):
    def _respond(self, method):
        code, ctype, body, cmd = dispatch(method, self.path, self.server.bridge.get_status)
        if cmd is not None:
            self.server.bridge.push_command(cmd)
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._respond('GET')

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        self._respond('POST')

    def log_message(self, *args):
        pass


def start_api_server(bridge, host, port):
    """Start the HTTP server on a daemon thread. Returns the server, or None if
    the port is unavailable — a missing API never crashes the display."""
    try:
        server = HTTPServer((host, port), _Handler)
    except (OSError, OverflowError) as exc:
        print('API server not started:', exc)
        return None
    server.bridge = bridge
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print('API server on http://' + host + ':' + str(port))
    return server
