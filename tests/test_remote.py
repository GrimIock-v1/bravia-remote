#!/usr/bin/env python3
"""End-to-end test: run the remote against a simulated BRAVIA and exercise every endpoint.

Usage: python3 tests/test_remote.py            (starts the app itself)
       python3 tests/test_remote.py --external http://host:port --mock-port 8099
         (app already running elsewhere, e.g. in Docker, pointed at the mock)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
EXTERNAL = args[args.index("--external") + 1] if "--external" in args else None
MOCK_PORT = int(args[args.index("--mock-port") + 1]) if "--mock-port" in args else 8099
APP_PORT = 8788
BASE = EXTERNAL or f"http://127.0.0.1:{APP_PORT}"
MOCK = f"http://127.0.0.1:{MOCK_PORT}"

failures = 0


def req(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"},
                               method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(name, cond, detail=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        failures += 1


def wait_for(url, secs=10):
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


procs = []
try:
    procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "mock_tv.py"), str(MOCK_PORT)]))
    if not EXTERNAL:
        cfg = tempfile.mkdtemp()
        env = dict(os.environ, CONFIG_DIR=cfg, TV_IP=f"127.0.0.1:{MOCK_PORT}",
                   PORT=str(APP_PORT))
        procs.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "tv_remote.py")],
                                      env=env, stdout=subprocess.DEVNULL))
    assert wait_for(MOCK + "/"), "mock TV did not start"
    assert wait_for(BASE + "/healthz"), "app did not start"

    s, body = req("/")
    check("serves remote page", s == 200 and "BRAVIA" in body, s)
    s, body = req("/healthz")
    check("healthz", s == 200, s)

    s, body = req("/api/status")
    st = json.loads(body)
    check("status reachable before pairing", st["reachable"] and st["power"] == "active", body)
    check("status reports unpaired", st["auth_ok"] is False, body)

    s, body = req("/api/key", {"key": "Up"})
    check("button refused before pairing", s == 401 and json.loads(body).get("auth") is False, body)

    s, body = req("/api/pair/start", {})
    check("pair start asks for PIN", s == 200 and json.loads(body)["paired"] is False, body)
    s, body = req("/api/pair/finish", {"pin": "0000"})
    check("wrong PIN rejected", s == 500, body)
    s, body = req("/api/pair/finish", {"pin": "1234"})
    check("correct PIN accepted", s == 200, body)

    st = json.loads(req("/api/status")[1])
    check("status after pairing has volume", st["auth_ok"] is True and st["volume"] == 20, st)
    check("status shows current input", st["playing"] == "HDMI 2", st)

    check("d-pad button", req("/api/key", {"key": "Up"})[0] == 200)
    check("volume button", req("/api/key", {"key": "VolumeUp"})[0] == 200)
    check("unknown button rejected", req("/api/key", {"key": "Nope"})[0] == 500)

    s, body = req("/api/inputs")
    inputs = json.loads(body).get("inputs", [])
    check("input list", len(inputs) == 2 and inputs[1]["title"] == "Apple TV", body)
    check("switch input", req("/api/input", {"uri": "extInput:hdmi?port=2"})[0] == 200)

    check("power off", req("/api/power", {"on": False})[0] == 200)
    check("standby reported", json.loads(req("/api/status")[1])["power"] == "standby")
    check("power on", req("/api/power", {"on": True})[0] == 200)
    check("active again", json.loads(req("/api/status")[1])["power"] == "active")

    log = json.loads(urllib.request.urlopen(MOCK + "/").read())["log"]
    check("TV received IRCC codes (TV's own code used for VolumeUp)",
          log[:2] == ["AAAAAQAAAAEAAAB0Aw==", "TVCODE_VOLUP"], log)
    check("TV received input switch", "input:extInput:hdmi?port=2" in log, log)
finally:
    for p in procs:
        p.terminate()

print(f"\n{'ALL TESTS PASSED' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
