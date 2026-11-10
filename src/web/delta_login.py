"""Short-lived QR tickets shared by the plugin and the web server threads."""

from __future__ import annotations

import secrets
import time
from threading import Lock

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()
_lock = Lock()
_tickets: dict[str, dict] = {}
_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
}


def create_ticket(image: str, ttl: int) -> str:
    with _lock:
        now = time.monotonic()
        for key in list(_tickets):
            if _tickets[key]["expires"] <= now:
                del _tickets[key]
        if len(_tickets) >= 64:
            raise ValueError("too many active login pages")
        ticket = secrets.token_urlsafe(32)
        _tickets[ticket] = {"image": image, "status": "pending", "message": "请使用对应客户端扫码，并在手机上确认登录。", "expires": now + ttl}
        return ticket


def finish_ticket(ticket: str, status: str, message: str) -> None:
    with _lock:
        entry = _tickets.get(ticket)
        if entry:
            entry.update(image="", status=status, message=message, expires=time.monotonic() + 300)


def read_ticket(ticket: str) -> dict | None:
    with _lock:
        entry = _tickets.get(ticket)
        if not entry:
            return None
        if entry["expires"] <= time.monotonic():
            del _tickets[ticket]
            return None
        return {key: entry[key] for key in ("image", "status", "message")}


@router.get("/delta-login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_PAGE, headers=_HEADERS)


@router.post("/delta-login/status")
async def login_status(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 512:
            return JSONResponse(
                {"status": "expired", "message": "无效的登录链接。"},
                status_code=400, headers=_HEADERS,
            )
    ticket = body.decode("ascii", errors="ignore")
    result = read_ticket(ticket) if 20 <= len(ticket) <= 100 else None
    return JSONResponse(result or {"status": "expired", "message": "链接已失效，请重新向机器人发送“三角洲 登录”。"}, headers=_HEADERS)


_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>三角洲扫码登录</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(at top left,#215568,#18223e 65%);color:#f4f7ff;font:16px/1.7 system-ui,sans-serif}main{box-sizing:border-box;width:min(92vw,440px);padding:28px;text-align:center;border:1px solid #ffffff30;border-radius:24px;background:#24364cce;backdrop-filter:blur(24px)}h1{font-size:24px}img{width:256px;max-width:100%;border:12px solid white;border-radius:12px;box-sizing:border-box}p{color:#d4e4f5}small{color:#b8cde1}#status{min-height:54px}button{padding:9px 20px;border:1px solid #99dfff;border-radius:9px;background:#22465b;color:white;cursor:pointer}</style>
<main><h1>三角洲扫码登录</h1><img id="qr" hidden alt="登录二维码"><p id="status" role="status" aria-live="polite">正在加载二维码…</p><button id="retry" hidden>重试连接</button><small>仅限本人使用，请勿转发链接或二维码。<br>链接临时有效，重启机器人后失效。</small></main>
<script>
const ticket=location.hash.slice(1);
const qr=document.getElementById('qr'),statusText=document.getElementById('status'),retry=document.getElementById('retry');let failures=0;
async function poll(){retry.hidden=true;try{const response=await fetch('/delta-login/status',{method:'POST',body:ticket,cache:'no-store',credentials:'omit'});if(!response.ok)throw new Error();const data=await response.json();failures=0;statusText.textContent=data.message;qr.hidden=data.status!=='pending'||!data.image;if(!qr.hidden)qr.src='data:image/png;base64,'+data.image;else qr.removeAttribute('src');if(data.status==='pending')setTimeout(poll,2000);}catch(_){failures++;statusText.textContent='连接暂时中断，请检查网络后重试。';if(failures<5)setTimeout(poll,3000);else retry.hidden=false;}}
retry.addEventListener('click',()=>{failures=0;poll();});poll();
</script></html>"""
