"""Isolated VEXA Gmail OAuth callback service. No mailbox or code forwarding."""
import os, secrets, json, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REDIRECT = "https://vexa-gmail-oauth-production.up.railway.app/oauth/callback"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        data=body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path=urllib.parse.urlsplit(self.path)
        if path.path == "/health":
            return self.reply(200,"OK")
        if path.path == "/":
            return self.reply(200,"VEXA Gmail OAuth service ready. Authorization is not enabled until credentials are configured.")
        if path.path == "/oauth/callback":
            # Deliberately do not exchange or log authorization codes before
            # encrypted persistent token storage and administrator validation exist.
            return self.reply(503,"OAuth callback endpoint is online, but Gmail authorization is not configured yet. No credentials were stored.")
        return self.reply(404,"Not found")

    def log_message(self, format, *args):
        # Never log URLs: OAuth callback URLs may contain sensitive codes.
        pass

if __name__ == "__main__":
    port=int(os.environ.get("PORT","8080"))
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
