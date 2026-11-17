"""Synthetic, loopback-only login site. No real identities or external resources."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
import json

SCENARIOS = {"valid", "invalid", "already", "expired", "changed", "challenge", "wrong_tenant", "unknown", "aria_variant"}

class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.sessions = {}
        self.base = f"http://127.0.0.1:{self.server_port}"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, body, status=200, cookie=None, json_type=False):
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if json_type else "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        if cookie:
            self.send_header("Set-Cookie", f"fixture={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body.encode())

    def redirect(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("fixture")
        return self.server.sessions.get(token.value if token else "")

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/start":
            scenario = parse_qs(u.query).get("scenario", ["valid"])[0]
            if scenario not in SCENARIOS:
                return self.respond("unknown", 400)
            token = uuid4().hex
            s = {"scenario": scenario, "authenticated": scenario == "already"}
            self.server.sessions[token] = s
            if scenario == "changed":
                body = '<a id="entry-alternate" href="/login">Access workspace</a>'
            elif scenario == "already":
                body = self.dashboard(s)
            elif scenario == "expired":
                body = '<h1 id="expired">Session expired</h1>'
            elif scenario == "unknown":
                body = '<h1>Unknown screen</h1>'
            else:
                body = self.form(scenario)
            return self.respond(body, cookie=token)
        if u.path == "/redirect-test" and hasattr(self.server, "redirect_target"):
            return self.redirect(self.server.redirect_target)
        s = self.session()
        if not s:
            return self.respond('<h1 id="expired">Session expired</h1>', 401)
        if u.path == "/login":
            return self.respond(self.form(s["scenario"]))
        if u.path == "/challenge":
            return self.respond('<h1 id="challenge">Additional verification</h1><form method="post" action="/complete"><button id="complete">Complete synthetic verification</button></form>')
        if u.path == "/dashboard" and s["authenticated"]:
            return self.respond(self.dashboard(s))
        if u.path == "/context":
            if not s["authenticated"]:
                return self.respond('{}', 401, json_type=True)
            tenant = "other-synthetic" if s["scenario"] == "wrong_tenant" else "synthetic-tenant"
            return self.respond(json.dumps({"tenant": tenant, "authenticated": True}), json_type=True)
        return self.respond("Unknown", 404)

    def do_POST(self):
        s = self.session()
        if not s:
            return self.respond('<h1 id="expired">Session expired</h1>', 401)
        body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4096)).decode()
        if self.path == "/complete" and s["scenario"] == "challenge":
            s["authenticated"] = True
            return self.redirect("/dashboard")
        fields = parse_qs(body)
        if self.path != "/login":
            return self.respond("Unknown", 404)
        valid = fields == {"account": ["synthetic"], "username": ["tester"], "password": ["fixture-only"]}
        if not valid or s["scenario"] == "invalid":
            return self.respond('<h1 id="rejected">Login rejected</h1>')
        if s["scenario"] == "challenge":
            return self.redirect("/challenge")
        s["authenticated"] = True
        return self.redirect("/dashboard")

    def form(self, scenario="valid"):
        html = '''<h1>Synthetic CEP Login</h1><form method="post" action="/login">
        <label>Account<input id="account" name="account" autocomplete="off"></label>
        <label>Username<input id="username" name="username" autocomplete="off"></label>
        <label>Password<input id="password" type="password" name="password" autocomplete="off"></label>
        <button id="submit">Continue securely</button></form>'''
        if scenario == 'changed':
            html = html.replace('Account<input', 'Workspace<input').replace('Username<input', 'Member<input').replace('Password<input', 'Access key<input').replace('Continue securely', 'Enter CEP')
        if scenario == 'aria_variant':
            html=html.replace('Account<input','Organization<input').replace('Username<input','Member email<input').replace('Password<input','Secret phrase<input').replace('Continue securely','Sign in')
        return html

    def dashboard(self, s):
        tenant = "other-synthetic" if s["scenario"] == "wrong_tenant" else "synthetic-tenant"
        return f'<main id="dashboard"><h1>Synthetic CEP dashboard</h1><span id="tenant">{tenant}</span><a id="readonly" href="/context">Read-only context</a></main>'

@contextmanager
def fixture_server():
    server = FixtureServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
