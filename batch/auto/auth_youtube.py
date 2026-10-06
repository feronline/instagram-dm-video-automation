"""Tek seferlik: YouTube OAuth izni al, refresh token'ı .env'ye yaz.
Önce Google Cloud'da OAuth istemcisi (Desktop app) oluştur, YOUTUBE_CLIENT_ID/SECRET'i .env'ye koy.
Çalıştır: python auto/auth_youtube.py
"""
import http.server, re, threading, urllib.parse, webbrowser
from common import *

PORT = 8765
cid, sec = env("YOUTUBE_CLIENT_ID", required=True), env("YOUTUBE_CLIENT_SECRET", required=True)
redirect = "http://127.0.0.1:%d/" % PORT
got = {}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got["code"] = (q.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("Tamam, bu pencereyi kapatabilirsin.".encode("utf-8"))

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": redirect, "response_type": "code", "access_type": "offline", "prompt": "consent",
    "scope": "https://www.googleapis.com/auth/youtube.upload"})
print("Tarayıcı açılıyor:", url)
webbrowser.open(url)
while "code" not in got:
    srv.handle_request()
r = http("POST", "https://oauth2.googleapis.com/token", data={
    "code": got["code"], "client_id": cid, "client_secret": sec, "redirect_uri": redirect, "grant_type": "authorization_code"})
rt = r["refresh_token"]
p = os.path.join(REPO, ".env")
s = open(p, encoding="utf-8").read()
s = re.sub(r"(?m)^YOUTUBE_REFRESH_TOKEN=.*$", "YOUTUBE_REFRESH_TOKEN=" + rt, s) if "YOUTUBE_REFRESH_TOKEN=" in s \
    else s.rstrip("\n") + "\nYOUTUBE_REFRESH_TOKEN=" + rt + "\n"
open(p, "w", encoding="utf-8").write(s)
print("Refresh token .env'ye yazıldı.")
