import json, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
S={"power":"active","vol":20,"log":[]}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def reply(self,code,obj=None,hdr=None,raw=b""):
        b=json.dumps(obj).encode() if obj is not None else raw
        self.send_response(code)
        for k,v in (hdr or {}).items(): self.send_header(k,v)
        self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_POST(self):
        n=int(self.headers.get("Content-Length") or 0);body=self.rfile.read(n)
        authed=self.headers.get("Cookie")=="auth=GOOD"
        if self.path=="/sony/IRCC":
            if not authed: return self.reply(403,raw=b"")
            S["log"].append(body.decode().split("<IRCCCode>")[1].split("<")[0]); return self.reply(200,raw=b"<ok/>")
        j=json.loads(body);m=j["method"]
        if m=="actRegister":
            a=self.headers.get("Authorization")
            if authed or a=="Basic "+base64.b64encode(b":1234").decode():
                return self.reply(200,{"result":[],"id":8},{"Set-Cookie":"auth=GOOD; Path=/sony/; Max-Age=1209600"})
            return self.reply(401,{"error":[401,"Unauthorized"]})
        if m=="getPowerStatus": return self.reply(200,{"result":[{"status":S["power"]}],"id":1})
        if not authed: return self.reply(200,{"error":[403,"Forbidden"],"id":1})
        if m=="getVolumeInformation": return self.reply(200,{"result":[[{"target":"speaker","volume":S["vol"],"mute":False}]],"id":1})
        if m=="getRemoteControllerInfo": return self.reply(200,{"result":[{"bundled":True},[{"name":"VolumeUp","value":"TVCODE_VOLUP"}]],"id":1})
        if m=="getPlayingContentInfo": return self.reply(200,{"result":[{"title":"HDMI 2","uri":"extInput:hdmi?port=2"}],"id":1})
        if m=="getCurrentExternalInputsStatus": return self.reply(200,{"result":[[{"uri":"extInput:hdmi?port=1","title":"HDMI 1","label":"","connection":True},{"uri":"extInput:hdmi?port=2","title":"HDMI 2","label":"Apple TV","connection":True}]],"id":1})
        if m=="setPlayContent": S["log"].append("input:"+j["params"][0]["uri"]); return self.reply(200,{"result":[],"id":1})
        if m=="setPowerStatus": S["power"]="active" if j["params"][0]["status"] else "standby"; return self.reply(200,{"result":[],"id":1})
        return self.reply(200,{"error":[12,"No such method"],"id":1})
    def do_GET(self):
        self.reply(200,S)
import sys
ThreadingHTTPServer(("127.0.0.1",int(sys.argv[1]) if len(sys.argv)>1 else 8099),H).serve_forever()
