"""Manage friendships of the currently logged-in bot account."""
import asyncio
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from core.logger_config import get_logger
from web.admin.shared import _require_sender, read_json_body, require_sender

router = APIRouter()
logger = get_logger("AdminFriends")
_response_lock = asyncio.Lock()


def _error(message: str, status: int = 400):
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _uid(value) -> str:
    return value.strip() if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value.strip()) else ""


def _mutation_guard(request: Request):
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        return _error("请从当前后台页面操作", 403)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return _error("请使用 JSON 提交操作", 415)
    return None


async def _profiles(sender, uids):
    try:
        return await sender.get_person_infos_batch(uids) if uids else {}
    except Exception:
        return {}


@router.get("/admin/api/friends")
@require_sender
async def list_friends(search: str = Query("", max_length=80), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    sender = _require_sender()
    try:
        items = await sender.get_friendship(strict=True)
    except Exception as exc:
        logger.warning("读取好友失败: %s", type(exc).__name__)
        return _error("读取好友列表失败，请稍后重试", 502)
    items = [item for item in items if isinstance(item, dict) and _uid(item.get("uid"))]
    online_count = sum(bool(item.get("online")) for item in items)
    total_count = len(items)
    needle = search.strip().casefold()
    filtered = [item for item in items if not needle or needle in str(item.get("name", "")).casefold() or needle in item["uid"].casefold()]
    page = filtered[offset:offset + limit]
    profiles = await _profiles(sender, [item["uid"] for item in page])
    rows = [{"uid": item["uid"], "name": item.get("name") or profiles.get(item["uid"], {}).get("name") or item["uid"],
             "online": bool(item.get("online"))} for item in page]
    return JSONResponse({"ok": True, "friends": rows, "total": len(filtered), "total_count": total_count,
                         "online_count": online_count, "offset": offset, "limit": limit})


@router.get("/admin/api/friends/requests")
@require_sender
async def list_requests(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    sender = _require_sender()
    try:
        items = await sender.get_friendship_requests(strict=True)
    except Exception as exc:
        logger.warning("读取好友申请失败: %s", type(exc).__name__)
        return _error("读取好友申请失败，请稍后重试", 502)
    valid = [item for item in items if isinstance(item, dict) and _uid(item.get("uid")) and str(item.get("friendRequestId", "")).isdigit() and int(item["friendRequestId"]) > 0]
    page = valid[offset:offset + limit]
    profiles = await _profiles(sender, [item["uid"] for item in page])
    rows = [{"uid": item["uid"], "request_id": str(item["friendRequestId"]),
             "name": profiles.get(item["uid"], {}).get("name") or item["uid"],
             "created_at": str(item.get("createTime") or "")} for item in page]
    return JSONResponse({"ok": True, "requests": rows, "total": len(valid), "offset": offset, "limit": limit})


@router.post("/admin/api/friends/requests/respond")
@require_sender
async def respond_request(request: Request):
    guard = _mutation_guard(request)
    if guard is not None:
        return guard
    body = await read_json_body(request)
    uid, raw_id, agree = _uid(body.get("uid")), body.get("request_id"), body.get("agree")
    if (not uid or isinstance(raw_id, bool) or not isinstance(raw_id, (str, int))
            or not re.fullmatch(r"[0-9]{1,20}", str(raw_id)) or int(raw_id) <= 0 or not isinstance(agree, bool)):
        return _error("申请人、申请编号或处理结果不合法")
    sender = _require_sender()
    async with _response_lock:
        try:
            pending = await sender.get_friendship_requests(strict=True)
            if not any(item.get("uid") == uid and str(item.get("friendRequestId")) == str(int(raw_id)) for item in pending):
                return _error("该申请已处理或已失效，请刷新列表", 409)
            result = await sender.post_friendship_response(uid, int(raw_id), agree)
        except Exception as exc:
            logger.warning("处理好友申请失败: %s", type(exc).__name__)
            return _error("处理好友申请失败，请刷新后重试", 502)
    if result.get("error") or result.get("status") is False:
        return _error("平台未能处理该申请，请刷新后重试", 502)
    return JSONResponse({"ok": True, "message": "已同意好友申请" if agree else "已拒绝好友申请"})


async def _is_friend(sender, uid):
    return any(item.get("uid") == uid for item in await sender.get_friendship(strict=True))


@router.get("/admin/api/friends/{uid}/remark")
@require_sender
async def get_remark(uid: str):
    if not _uid(uid):
        return _error("好友 UID 不合法")
    sender = _require_sender()
    try:
        if not await _is_friend(sender, uid):
            return _error("该用户已不在好友列表中，请刷新", 404)
        remark = await sender.get_user_remark_name(uid)
    except Exception as exc:
        logger.warning("读取好友备注失败: %s", type(exc).__name__)
        return _error("读取好友备注失败，请稍后重试", 502)
    return JSONResponse({"ok": True, "remark": remark})


@router.post("/admin/api/friends/{uid}/remark")
@require_sender
async def set_remark(uid: str, request: Request):
    guard = _mutation_guard(request)
    if guard is not None:
        return guard
    body = await read_json_body(request)
    remark = body.get("remark")
    if not _uid(uid):
        return _error("好友 UID 不合法")
    if not isinstance(remark, str) or len(remark) > 50 or any(ord(char) < 32 or ord(char) == 127 for char in remark):
        return _error("备注最多 50 字，不能包含换行或控制字符")
    sender = _require_sender()
    try:
        if not await _is_friend(sender, uid):
            return _error("该用户已不在好友列表中，请刷新", 404)
        result = await sender.set_user_remark_name(uid, remark)
    except Exception as exc:
        logger.warning("修改好友备注失败: %s", type(exc).__name__)
        return _error("修改备注失败，请稍后重试", 502)
    if result.get("error") or result.get("status") is False:
        return _error("平台未能保存备注，请稍后重试", 502)
    return JSONResponse({"ok": True, "message": "备注已更新" if remark else "备注已清除"})
