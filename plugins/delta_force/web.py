"""Temporary capability links. No API credentials or session IDs reach the browser."""
import time
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse, Response

PREFIX = "/_plugins/delta_force/login"
HEADERS = {
    "Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


class LoginWeb:
    def __init__(self, plugin, app):
        self.plugin, self.app = plugin, app
        self.routes = []
        for suffix, endpoint in (("", self.page), ("/status", self.status), ("/qr", self.qr)):
            app.add_api_route(PREFIX + "/{token}" + suffix, endpoint, methods=["GET"], include_in_schema=False)
            self.routes.append(app.router.routes[-1])

    def close(self):
        for route in self.routes:
            if route in self.app.router.routes:
                self.app.router.routes.remove(route)
        self.routes.clear()

    def get(self, token):
        item = self.plugin.pages.get(token)
        if not item or time.time() > item["expires"]:
            return None
        return item

    async def page(self, token: str):
        if not self.get(token):
            return HTMLResponse("登录链接已失效，请重新发送 三角洲 登录。", status_code=410, headers=HEADERS)
        return HTMLResponse(Path(__file__).with_name("login.html").read_text(encoding="utf-8"), headers=HEADERS)

    async def status(self, token: str):
        item = self.get(token)
        if not item:
            return JSONResponse({"state": "expired", "message": "链接已失效，请重新发起登录。", "terminal": True}, status_code=410, headers=HEADERS)
        return JSONResponse({"state": item["state"], "message": item["message"],
                             "terminal": item["terminal"], "provider": item["provider"]}, headers=HEADERS)

    async def qr(self, token: str):
        item = self.get(token)
        if not item or item["terminal"]:
            return Response(status_code=410, headers=HEADERS)
        return Response(item["qr"], media_type=item["mime"], headers=HEADERS)
