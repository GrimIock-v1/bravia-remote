#!/usr/bin/env python3
"""
Bravia Remote — a tiny web remote for older Sony BRAVIA TVs (e.g. KDL-70W840B).

Run:   python3 tv_remote.py                (defaults to TV at 10.0.0.61)
       python3 tv_remote.py --tv 10.0.0.61 --port 8787
Then open the printed URL on your phone (same Wi-Fi).

No dependencies beyond the Python 3 standard library.
Pairing info is saved to ~/.bravia_remote.json, or $CONFIG_DIR/bravia_remote.json
when CONFIG_DIR is set (the Docker image uses /config).

Environment variables (Docker): TV_IP, TV_MAC, PORT, CONFIG_DIR.
"""
import argparse
import base64
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_CONFIG_DIR = os.environ.get("CONFIG_DIR")
CONFIG_PATH = (os.path.join(_CONFIG_DIR, "bravia_remote.json") if _CONFIG_DIR
               else os.path.expanduser("~/.bravia_remote.json"))
DEFAULT_TV = "10.0.0.61"
DEFAULT_MAC = "00:71:cc:4b:a0:63"
NICKNAME = "Bravia Remote (web)"

# Standard Sony IRCC codes; replaced by the TV's own list when it provides one.
FALLBACK_CODES = {
    "Power": "AAAAAQAAAAEAAAAVAw==", "PowerOff": "AAAAAQAAAAEAAAAvAw==",
    "Up": "AAAAAQAAAAEAAAB0Aw==", "Down": "AAAAAQAAAAEAAAB1Aw==",
    "Left": "AAAAAQAAAAEAAAA0Aw==", "Right": "AAAAAQAAAAEAAAAzAw==",
    "Confirm": "AAAAAQAAAAEAAABlAw==", "Home": "AAAAAQAAAAEAAABgAw==",
    "Return": "AAAAAgAAAJcAAAAjAw==", "Options": "AAAAAgAAAJcAAAA2Aw==",
    "VolumeUp": "AAAAAQAAAAEAAAASAw==", "VolumeDown": "AAAAAQAAAAEAAAATAw==",
    "Mute": "AAAAAQAAAAEAAAAUAw==", "ChannelUp": "AAAAAQAAAAEAAAAQAw==",
    "ChannelDown": "AAAAAQAAAAEAAAARAw==", "Input": "AAAAAQAAAAEAAAAlAw==",
    "Play": "AAAAAgAAAJcAAAAaAw==", "Pause": "AAAAAgAAAJcAAAAZAw==",
    "Stop": "AAAAAgAAAJcAAAAYAw==", "Rewind": "AAAAAgAAAJcAAAAbAw==",
    "Forward": "AAAAAgAAAJcAAAAcAw==", "Display": "AAAAAQAAAAEAAAA6Aw==",
    "GGuide": "AAAAAQAAAKQAAABbAw==",
    "Num1": "AAAAAQAAAAEAAAAAAw==", "Num2": "AAAAAQAAAAEAAAABAw==",
    "Num3": "AAAAAQAAAAEAAAACAw==", "Num4": "AAAAAQAAAAEAAAADAw==",
    "Num5": "AAAAAQAAAAEAAAAEAw==", "Num6": "AAAAAQAAAAEAAAAFAw==",
    "Num7": "AAAAAQAAAAEAAAAGAw==", "Num8": "AAAAAQAAAAEAAAAHAw==",
    "Num9": "AAAAAQAAAAEAAAAIAw==", "Num0": "AAAAAQAAAAEAAAAJAw==",
}

IRCC_BODY = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body><u:X_SendIRCC xmlns:u=\"urn:schemas-sony-com:service:IRCC:1\">"
    "<IRCCCode>{code}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>"
)


class AuthError(Exception):
    pass


class TV:
    def __init__(self, ip, mac):
        self.lock = threading.Lock()
        self.cfg = self._load()
        self.cfg.setdefault("client_id", f"bravia-remote:{uuid.uuid4()}")
        self.ip = ip or self.cfg.get("tv_ip") or DEFAULT_TV
        self.mac = mac or self.cfg.get("mac") or DEFAULT_MAC
        self.cfg["tv_ip"], self.cfg["mac"] = self.ip, self.mac
        self.codes = dict(FALLBACK_CODES)
        self.codes_loaded = False
        self.auth_ok = None  # None = unknown yet
        self._save()

    # ---------- config ----------
    def _load(self):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.cfg, f, indent=2)
            os.chmod(CONFIG_PATH, 0o600)
        except Exception as e:
            print(f"warning: could not save {CONFIG_PATH}: {e}", file=sys.stderr)

    # ---------- HTTP to TV ----------
    def _headers(self):
        h = {}
        if self.cfg.get("cookie"):
            h["Cookie"] = self.cfg["cookie"]
        if self.cfg.get("psk"):
            h["X-Auth-PSK"] = self.cfg["psk"]
        return h

    def _post(self, path, body, headers, timeout=4):
        req = urllib.request.Request(
            f"http://{self.ip}{path}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def call(self, service, method, params=None, version="1.0", timeout=4):
        payload = json.dumps(
            {"method": method, "params": params if params is not None else [],
             "id": 1, "version": version}
        ).encode()
        h = {"Content-Type": "application/json", **self._headers()}
        status, _, raw = self._post(f"/sony/{service}", payload, h, timeout)
        if status in (401, 403):
            self.auth_ok = False
            raise AuthError(f"{service}.{method} needs pairing")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise RuntimeError(f"bad response from TV ({status})")
        if "error" in data:
            code = data["error"][0] if data["error"] else None
            if code in (401, 403, 40000):
                self.auth_ok = False
                raise AuthError(f"{service}.{method} needs pairing")
            raise RuntimeError(f"{service}.{method}: {data['error']}")
        return data.get("result", [])

    def ircc(self, name):
        code = self.codes.get(name)
        if not code:
            raise RuntimeError(f"unknown button {name}")
        h = {
            "Content-Type": 'text/xml; charset=UTF-8',
            "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"',
            **self._headers(),
        }
        status, _, raw = self._post(
            "/sony/IRCC", IRCC_BODY.format(code=code).encode(), h
        )
        if status in (401, 403):
            self.auth_ok = False
            raise AuthError("pairing required")
        if status >= 400:
            raise RuntimeError(f"TV rejected {name} ({status})")
        self.auth_ok = True

    def load_codes(self):
        if self.codes_loaded:
            return
        try:
            res = self.call("system", "getRemoteControllerInfo")
            for item in res[1] if len(res) > 1 else []:
                if item.get("name") and item.get("value"):
                    self.codes[item["name"]] = item["value"]
            self.codes_loaded = True
        except Exception:
            pass

    # ---------- pairing ----------
    def _register_body(self):
        return json.dumps({
            "method": "actRegister", "id": 8, "version": "1.0",
            "params": [
                {"clientid": self.cfg["client_id"], "nickname": NICKNAME,
                 "level": "private"},
                [{"value": "yes", "function": "WOL"}],
            ],
        }).encode()

    def _store_cookie(self, headers):
        for k, v in headers.items():
            if k.lower() == "set-cookie" and v.startswith("auth="):
                self.cfg["cookie"] = v.split(";")[0]
                self._save()
                return True
        return False

    def pair_start(self):
        """Ask the TV to show a PIN. Returns True if already authorised."""
        h = {"Content-Type": "application/json"}
        status, headers, _ = self._post("/sony/accessControl",
                                        self._register_body(), h)
        if status == 200:  # TV accepted without PIN
            self._store_cookie(headers)
            self.auth_ok = True
            return True
        return False

    def pair_finish(self, pin):
        token = base64.b64encode(f":{pin}".encode()).decode()
        h = {"Content-Type": "application/json", "Authorization": f"Basic {token}"}
        status, headers, raw = self._post("/sony/accessControl",
                                          self._register_body(), h)
        if status != 200 or not self._store_cookie(headers):
            raise RuntimeError("PIN not accepted — try again")
        self.auth_ok = True
        self.codes_loaded = False
        return True

    def renew(self):
        """Refresh the auth cookie (it expires after ~2 weeks)."""
        if not self.cfg.get("cookie"):
            return
        h = {"Content-Type": "application/json", "Cookie": self.cfg["cookie"]}
        try:
            status, headers, _ = self._post("/sony/accessControl",
                                            self._register_body(), h)
            if status == 200:
                self._store_cookie(headers)
        except Exception:
            pass

    def set_psk(self, psk):
        self.cfg["psk"] = psk.strip() or None
        self._save()
        self.auth_ok = None

    # ---------- power ----------
    def wake(self):
        mac = bytes.fromhex(self.mac.replace(":", "").replace("-", ""))
        packet = b"\xff" * 6 + mac * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for target in ("255.255.255.255", self.ip):
                try:
                    s.sendto(packet, (target, 9))
                except OSError:
                    pass
        try:
            self.call("system", "setPowerStatus", [{"status": True}], timeout=2)
        except Exception:
            pass

    def power_off(self):
        try:
            self.call("system", "setPowerStatus", [{"status": False}])
        except AuthError:
            raise
        except Exception:
            self.ircc("PowerOff")

    # ---------- status ----------
    def status(self):
        out = {"tv": self.ip, "reachable": False, "power": "unknown",
               "auth_ok": self.auth_ok, "volume": None, "muted": None,
               "playing": None}
        try:
            res = self.call("system", "getPowerStatus", timeout=2)
            out["reachable"] = True
            out["power"] = (res[0] or {}).get("status", "unknown")
        except AuthError:
            out["reachable"] = True
        except Exception:
            return out
        if out["power"] == "active":
            try:
                for v in self.call("audio", "getVolumeInformation")[0]:
                    if v.get("target") in ("speaker", ""):
                        out["volume"], out["muted"] = v.get("volume"), v.get("mute")
                        break
                if self.auth_ok is None:
                    self.auth_ok = True
            except AuthError:
                pass
            except Exception:
                pass
            try:
                p = self.call("avContent", "getPlayingContentInfo")[0]
                out["playing"] = p.get("title") or p.get("source") or p.get("uri")
            except Exception:
                pass
        out["auth_ok"] = self.auth_ok
        return out

    def inputs(self):
        res = self.call("avContent", "getCurrentExternalInputsStatus")
        return [{"uri": i.get("uri"), "title": i.get("label") or i.get("title"),
                 "connected": i.get("connection")} for i in (res[0] if res else [])]

    def set_input(self, uri):
        self.call("avContent", "setPlayContent", [{"uri": uri}])

    def set_volume(self, value):
        self.call("audio", "setAudioVolume",
                  [{"target": "speaker", "volume": str(value)}])


# =====================================================================
#  Web UI
# =====================================================================
PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0e0f12">
<title>Bravia Remote</title>
<style>
:root{--bg:#0e0f12;--panel:#17191e;--key:#22252c;--key2:#2b2f38;--txt:#e9ebef;--dim:#8a909c;--acc:#4f8cff;--red:#ff5a5f;--ok:#3ecf8e}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none}
html,body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:420px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 14px) 18px calc(env(safe-area-inset-bottom) + 20px)}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.title{font-weight:650;font-size:17px}
.sub{color:var(--dim);font-size:12.5px;margin-top:2px;min-height:16px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--dim);margin-right:6px;vertical-align:1px}
.dot.on{background:var(--ok)}.dot.off{background:var(--red)}
button{font:inherit;color:var(--txt);background:var(--key);border:0;border-radius:14px;cursor:pointer;transition:transform .06s,background .1s}
button:active,button.pressed{transform:scale(.94);background:var(--key2)}
.power{width:48px;height:48px;border-radius:50%;background:#2a1a1c;color:var(--red);font-size:20px}
.row{display:grid;gap:10px;margin-bottom:10px}
.r3{grid-template-columns:repeat(3,1fr)}.r4{grid-template-columns:repeat(4,1fr)}
.row button{height:48px}
.pad{position:relative;width:250px;height:250px;margin:18px auto 20px;border-radius:50%;background:var(--panel)}
.pad button{position:absolute;background:transparent;font-size:22px;color:var(--dim);border-radius:50%}
.pad button:active{background:var(--key)}
.pad .u{top:6px;left:85px;width:80px;height:70px}.pad .d{bottom:6px;left:85px;width:80px;height:70px}
.pad .l{left:6px;top:85px;width:70px;height:80px}.pad .r{right:6px;top:85px;width:70px;height:80px}
.pad .ok{top:75px;left:75px;width:100px;height:100px;background:var(--key);color:var(--txt);font-size:16px;font-weight:600}
.rocker{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:6px 0 10px}
.rock{background:var(--panel);border-radius:18px;display:flex;flex-direction:column;align-items:center;padding:6px}
.rock button{width:100%;height:48px;background:transparent;font-size:22px}
.rock span{color:var(--dim);font-size:12px;padding:2px 0}
.volbar{height:4px;background:var(--key);border-radius:2px;margin:4px 0 14px;overflow:hidden}
.volbar i{display:block;height:100%;width:0;background:var(--acc);transition:width .2s}
.toggle{width:100%;background:transparent;color:var(--dim);font-size:13px;height:34px}
#nums{display:none}
.sheet{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:flex-end;justify-content:center;z-index:5}
.sheet.show{display:flex}
.card{background:var(--panel);width:100%;max-width:420px;border-radius:20px 20px 0 0;padding:20px 18px calc(env(safe-area-inset-bottom) + 20px)}
.card h3{margin:0 0 6px;font-size:17px}.card p{color:var(--dim);margin:0 0 14px;font-size:13.5px}
.card input{width:100%;font:inherit;font-size:22px;letter-spacing:6px;text-align:center;padding:12px;border-radius:12px;border:1px solid var(--key2);background:var(--bg);color:var(--txt);margin-bottom:12px;user-select:text;-webkit-user-select:text}
.card .primary{width:100%;height:48px;background:var(--acc);font-weight:600}
.card .link{width:100%;height:40px;background:transparent;color:var(--dim);margin-top:6px}
.list button{width:100%;height:50px;margin-bottom:8px;text-align:left;padding:0 16px;display:flex;justify-content:space-between;align-items:center}
.list small{color:var(--dim)}
.toast{position:fixed;left:50%;bottom:calc(env(safe-area-inset-bottom) + 24px);transform:translateX(-50%);background:#000c;padding:9px 14px;border-radius:10px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:9}
.toast.show{opacity:1}
</style></head><body>
<div class="wrap">
  <header>
    <div><div class="title">BRAVIA</div><div class="sub" id="sub"><span class="dot"></span>Connecting…</div></div>
    <button class="power" id="pwr" aria-label="Power">⏻</button>
  </header>

  <div class="row r3">
    <button data-k="Input">Input</button>
    <button data-k="Home">Home</button>
    <button data-k="Options">Options</button>
  </div>

  <div class="pad">
    <button class="u" data-k="Up" data-rep>▲</button>
    <button class="l" data-k="Left" data-rep>◀</button>
    <button class="ok" data-k="Confirm">OK</button>
    <button class="r" data-k="Right" data-rep>▶</button>
    <button class="d" data-k="Down" data-rep>▼</button>
  </div>

  <div class="row r3">
    <button data-k="Return">Back</button>
    <button data-k="Mute">Mute</button>
    <button data-k="Display">Info</button>
  </div>

  <div class="rocker">
    <div class="rock"><button data-k="VolumeUp" data-rep>＋</button><span id="voltxt">VOL</span><button data-k="VolumeDown" data-rep>－</button></div>
    <div class="rock"><button data-k="ChannelUp">＋</button><span>CH</span><button data-k="ChannelDown">－</button></div>
  </div>
  <div class="volbar"><i id="volbar"></i></div>

  <div class="row r4">
    <button data-k="Rewind">◀◀</button><button data-k="Play">▶</button>
    <button data-k="Pause">❚❚</button><button data-k="Forward">▶▶</button>
  </div>

  <button class="toggle" id="sources">Choose input source…</button>
  <button class="toggle" id="numtoggle">Show number pad</button>
  <div id="nums">
    <div class="row r3"><button data-k="Num1">1</button><button data-k="Num2">2</button><button data-k="Num3">3</button></div>
    <div class="row r3"><button data-k="Num4">4</button><button data-k="Num5">5</button><button data-k="Num6">6</button></div>
    <div class="row r3"><button data-k="Num7">7</button><button data-k="Num8">8</button><button data-k="Num9">9</button></div>
    <div class="row r3"><button data-k="GGuide">Guide</button><button data-k="Num0">0</button><button data-k="Stop">■</button></div>
  </div>
</div>

<div class="sheet" id="pair"><div class="card">
  <h3>Pair with your TV</h3>
  <p id="pairmsg">Tap “Show PIN” and a 4-digit code will appear on the TV screen.</p>
  <input id="pin" inputmode="numeric" maxlength="4" placeholder="••••" autocomplete="one-time-code">
  <button class="primary" id="pairgo">Show PIN on TV</button>
  <button class="link" id="pskbtn">Use a pre-shared key instead</button>
  <button class="link" id="pairclose">Not now</button>
</div></div>

<div class="sheet" id="inputs"><div class="card">
  <h3>Input source</h3><div class="list" id="inlist"><p>Loading…</p></div>
  <button class="link" id="inclose">Close</button>
</div></div>

<div class="toast" id="toast"></div>

<script>
const $=s=>document.querySelector(s);
let state={}, pinShown=false, pairDismissed=false;
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),1800)}
async function api(path,body){
  const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const j=await r.json().catch(()=>({error:'Bad response'}));
  if(j.auth===false){openPair();}
  if(!r.ok||j.error) throw new Error(j.error||('HTTP '+r.status));
  return j;
}
function buzz(){if(navigator.vibrate)navigator.vibrate(8)}
async function key(k){buzz();try{await api('key',{key:k});if(/Volume|Mute/.test(k))setTimeout(refresh,250)}catch(e){toast(e.message)}}

// buttons with press-and-hold repeat
document.querySelectorAll('[data-k]').forEach(b=>{
  let t1,t2;
  const stop=()=>{clearTimeout(t1);clearInterval(t2);b.classList.remove('pressed')};
  b.addEventListener('pointerdown',e=>{e.preventDefault();b.classList.add('pressed');key(b.dataset.k);
    if(b.hasAttribute('data-rep'))t1=setTimeout(()=>{t2=setInterval(()=>key(b.dataset.k),160)},420)});
  ['pointerup','pointerleave','pointercancel'].forEach(ev=>b.addEventListener(ev,stop));
});

$('#pwr').onclick=async()=>{buzz();
  const on=state.power!=='active';
  try{await api('power',{on});toast(on?'Waking TV…':'Turning off');setTimeout(refresh,on?3000:1200)}catch(e){toast(e.message)}};

$('#numtoggle').onclick=()=>{const n=$('#nums'),s=n.style.display==='block';n.style.display=s?'none':'block';$('#numtoggle').textContent=s?'Show number pad':'Hide number pad'};

$('#sources').onclick=async()=>{$('#inputs').classList.add('show');const L=$('#inlist');L.innerHTML='<p>Loading…</p>';
  try{const j=await api('inputs');L.innerHTML='';
    if(!j.inputs.length)L.innerHTML='<p>No inputs reported.</p>';
    j.inputs.forEach(i=>{const b=document.createElement('button');b.innerHTML=`<span>${i.title||i.uri}</span><small>${i.connected?'connected':''}</small>`;
      b.onclick=async()=>{try{await api('input',{uri:i.uri});$('#inputs').classList.remove('show');toast(i.title)}catch(e){toast(e.message)}};L.appendChild(b)})
  }catch(e){L.innerHTML='<p>'+e.message+'</p>'}};
$('#inclose').onclick=()=>$('#inputs').classList.remove('show');

function openPair(){if(pairDismissed)return;$('#pair').classList.add('show')}
$('#pairclose').onclick=()=>{pairDismissed=true;$('#pair').classList.remove('show')};
$('#pairgo').onclick=async()=>{
  try{
    if(!pinShown){const j=await api('pair/start',{});
      if(j.paired){$('#pair').classList.remove('show');toast('Paired');refresh();return}
      pinShown=true;$('#pairmsg').textContent='Enter the PIN shown on the TV.';$('#pairgo').textContent='Pair';$('#pin').focus();}
    else{await api('pair/finish',{pin:$('#pin').value.trim()});$('#pair').classList.remove('show');toast('Paired!');refresh()}
  }catch(e){toast(e.message)}};
$('#pskbtn').onclick=async()=>{const k=prompt('Pre-shared key (set on the TV under IP Control):');if(k==null)return;
  try{await api('psk',{psk:k});$('#pair').classList.remove('show');toast('Key saved');refresh()}catch(e){toast(e.message)}};

async function refresh(){
  try{state=await api('status');}catch(e){state={reachable:false}}
  const d=state.reachable?(state.power==='active'?'on':'off'):'';
  const label=!state.reachable?'TV not reachable':state.power==='active'?(state.playing||'On'):'Standby';
  $('#sub').innerHTML=`<span class="dot ${d}"></span>${label}`;
  if(state.volume!=null){$('#volbar').style.width=Math.min(100,state.volume)+'%';$('#voltxt').textContent=state.muted?'MUTED':state.volume}
  else{$('#voltxt').textContent='VOL'}
  if(state.auth_ok===false)openPair();
}
refresh();setInterval(()=>{if(!document.hidden)refresh()},5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
</script></body></html>"""


def make_handler(tv):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, obj=None, html=None):
            body = html.encode() if html is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type",
                             "text/html; charset=utf-8" if html else "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            p = self.path.split("?")[0]
            if p in ("/", "/index.html"):
                return self._send(200, html=PAGE)
            if p == "/healthz":
                return self._send(200, {"ok": True})
            self._route(p, None)

        def do_POST(self):
            self._route(self.path.split("?")[0], self._body())

        def _route(self, p, body):
            try:
                with tv.lock:
                    if p == "/api/status":
                        return self._send(200, tv.status())
                    if p == "/api/key" and body:
                        tv.load_codes()
                        tv.ircc(body.get("key", ""))
                        return self._send(200, {"ok": True})
                    if p == "/api/power" and body is not None:
                        tv.wake() if body.get("on") else tv.power_off()
                        return self._send(200, {"ok": True})
                    if p == "/api/inputs":
                        return self._send(200, {"inputs": tv.inputs()})
                    if p == "/api/input" and body:
                        tv.set_input(body["uri"])
                        return self._send(200, {"ok": True})
                    if p == "/api/volume" and body:
                        tv.set_volume(body["value"])
                        return self._send(200, {"ok": True})
                    if p == "/api/pair/start":
                        return self._send(200, {"paired": tv.pair_start()})
                    if p == "/api/pair/finish" and body:
                        tv.pair_finish(str(body.get("pin", "")))
                        return self._send(200, {"ok": True})
                    if p == "/api/psk" and body is not None:
                        tv.set_psk(body.get("psk", ""))
                        return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})
            except AuthError as e:
                return self._send(401, {"error": str(e), "auth": False})
            except (urllib.error.URLError, socket.timeout, OSError):
                return self._send(504, {"error": "TV not responding — is it on?"})
            except Exception as e:
                return self._send(500, {"error": str(e)})
    return H


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description="Web remote for Sony BRAVIA TVs")
    env = os.environ
    ap.add_argument("--tv", default=env.get("TV_IP") or None,
                    help=f"TV IP address (env TV_IP, default {DEFAULT_TV})")
    ap.add_argument("--mac", default=env.get("TV_MAC") or None,
                    help="TV MAC address for wake-on-LAN (env TV_MAC)")
    ap.add_argument("--port", type=int, default=int(env.get("PORT") or 8787),
                    help="web port (env PORT, default 8787)")
    a = ap.parse_args()

    tv = TV(a.tv, a.mac)
    tv.renew()

    def keep_fresh():
        while True:
            time.sleep(24 * 3600)
            with tv.lock:
                tv.renew()
    threading.Thread(target=keep_fresh, daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(tv))
    print(f"\n  Bravia Remote → TV {tv.ip}")
    print(f"  On this computer:  http://localhost:{a.port}")
    print(f"  On your phone:     http://{lan_ip()}:{a.port}\n")
    print("  Ctrl+C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
