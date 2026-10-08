"""Private, read-only Gmail OAuth connection for the store administrator.

No mailbox contents, login codes, or email addresses are sent to customers.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from cryptography.fernet import Fernet

REDIRECT = "https://vexa-gmail-oauth-production.up.railway.app/oauth/callback"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DATA_PATH = "/data/gmail_token.enc"

def config_ready():
    return all(os.environ.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OAUTH_ADMIN_KEY", "OAUTH_STATE_KEY", "OAUTH_ENCRYPTION_KEY"))

def http_json(url, form=None, bearer=None):
    headers = {"Accept": "application/json"}
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

def sign_state():
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    payload = timestamp + "." + nonce
    signature = hmac.new(os.environ["OAUTH_STATE_KEY"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature

def verify_state(state):
    try:
        timestamp, nonce, signature = state.split(".")
        payload = timestamp + "." + nonce
        expected = hmac.new(os.environ["OAUTH_STATE_KEY"].encode(), payload.encode(), hashlib.sha256).hexdigest()
        return abs(time.time() - int(timestamp)) <= 600 and hmac.compare_digest(signature, expected)
    except (ValueError, AttributeError):
        return False

def cipher():
    return Fernet(os.environ["OAUTH_ENCRYPTION_KEY"].encode())

def save_token(token):
    if not token.get("refresh_token"):
        raise ValueError("Google did not issue a refresh token")
    os.makedirs("/data", exist_ok=True)
    path = DATA_PATH + ".tmp"
    with open(path, "wb") as handle:
        handle.write(cipher().encrypt(json.dumps({"refresh_token": token["refresh_token"]}).encode()))
    os.chmod(path, 0o600)
    os.replace(path, DATA_PATH)

def read_token():
    with open(DATA_PATH, "rb") as handle:
        return json.loads(cipher().decrypt(handle.read()))

class Handler(BaseHTTPRequestHandler):
    def respond(self, status, message, headers=None):
        data = message.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        header = self.headers.get("Authorization", "")
        expected = os.environ.get("OAUTH_ADMIN_KEY", "")
        if not expected or not header.startswith("Basic "):
            return False
        try:
            username, password = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
            return hmac.compare_digest(username, "admin") and hmac.compare_digest(password, expected)
        except (ValueError, UnicodeError):
            return False

    def admin_only(self):
        if self.authorized():
            return True
        self.respond(401, "Administrator access required", {"WWW-Authenticate": 'Basic realm="VEXA Gmail setup"'})
        return False

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/health":
            return self.respond(200, "OK")
        if path == "/":
            return self.respond(200, "VEXA Gmail read-only OAuth service. Setup requires administrator access.")
        if path == "/connect":
            if not self.admin_only():
                return
            if not config_ready():
                return self.respond(503, "OAuth configuration incomplete")
            params = {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": sign_state(),
            }
            return self.respond(302, "Redirecting to Google", {"Location": "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)})
        if path == "/oauth/callback":
            if not config_ready():
                return self.respond(503, "OAuth configuration incomplete")
            query = urllib.parse.parse_qs(parsed.query)
            if not verify_state(query.get("state", [""])[0]):
                return self.respond(400, "Invalid or expired OAuth state")
            if "error" in query:
                return self.respond(400, "Google authorization was cancelled or denied")
            code = query.get("code", [""])[0]
            if not code:
                return self.respond(400, "Missing authorization code")
            try:
                token = http_json("https://oauth2.googleapis.com/token", {
                    "code": code,
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "redirect_uri": REDIRECT,
                    "grant_type": "authorization_code",
                })
                save_token(token)
            except Exception:
                return self.respond(502, "Unable to complete OAuth or save token. Check server configuration.")
            return self.respond(200, "Gmail connected with read-only access. You may close this page.")
        if path == "/status":
            if not self.admin_only():
                return
            return self.respond(200, json.dumps({"configured": config_ready(), "connected": os.path.isfile(DATA_PATH)}))
        if path == "/test":
            if not self.admin_only():
                return
            if not config_ready() or not os.path.isfile(DATA_PATH):
                return self.respond(409, "Gmail not connected")
            try:
                refresh = read_token()["refresh_token"]
                token = http_json("https://oauth2.googleapis.com/token", {
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                })["access_token"]
                # Verify read permission without exposing email bodies or codes.
                result = http_json("https://gmail.googleapis.com/gmail/v1/users/me/labels", bearer=token)
                count = len(result.get("labels", []))
            except Exception:
                return self.respond(502, "Gmail read-only test failed; check Google consent and credentials.")
            return self.respond(200, "Gmail read-only access verified. Label count: " + str(count))
        return self.respond(404, "Not found")

    def log_message(self, fmt, *args):
        # Never log OAuth codes, state, tokens, or sensitive request paths.
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
