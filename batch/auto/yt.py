"""YouTube Data API v3 yükleme (OAuth refresh token ile, yalnızca stdlib).
İlk kurulum: python auto/auth_youtube.py  (tarayıcıda izin ver -> refresh token .env'ye yazılır)
Uyarı: API projesi denetimden (audit) geçmeden yüklenen videolar private'a kilitlenir; günlük kota ~6 yükleme.
"""
import json, os, urllib.request, urllib.error
from common import *


def access_token():
    r = http("POST", "https://oauth2.googleapis.com/token", data={
        "client_id": env("YOUTUBE_CLIENT_ID", required=True), "client_secret": env("YOUTUBE_CLIENT_SECRET", required=True),
        "refresh_token": env("YOUTUBE_REFRESH_TOKEN", required=True), "grant_type": "refresh_token"})
    return r["access_token"]


def upload_short(video_path, title, description, tags=None):
    tok = access_token()
    size = os.path.getsize(video_path)
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "categoryId": "27",
                        "defaultLanguage": "tr", "defaultAudioLanguage": "tr", "tags": (tags or [])[:15]},
            "status": {"privacyStatus": env("YT_PRIVACY", "public"), "selfDeclaredMadeForKids": False}}
    init = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        loc = urllib.request.urlopen(init, timeout=60).headers["Location"]
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"))
    put = urllib.request.Request(loc, data=open(video_path, "rb").read(), method="PUT",
                                 headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    try:
        r = json.loads(urllib.request.urlopen(put, timeout=900).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"))
    return r["id"]
