from __future__ import annotations

from typing import Annotated, Any, Mapping

from pydantic import BaseModel as Bm, BeforeValidator, ConfigDict

from oopz_sdk.utils.payload import coerce_bool


def _empty_mute_deadline(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# 禁言/禁麦截止时间统一为整数毫秒，兼容接口下发的数字字符串和空值。
MuteDeadline = Annotated[int | None, BeforeValidator(_empty_mute_deadline)]


class BaseModel(Bm):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )


class OperationResult(BaseModel):
    ok: bool = True
    message: str = ""

    @classmethod
    def from_api(cls, data: Any) -> "OperationResult":
        if data is None:
            return cls.model_validate({"ok": True, "message": ""})
        if isinstance(data, bool):
            return cls.model_validate({"ok": data})
        if isinstance(data, Mapping):
            normalized = dict(data)
            # status 可能是 bool、整数或字符串（包括 "false"/"0"），严格转换
            # 避免 Python 真值把 "false" 当成功。
            # - key 不存在：get 返回 True → 按成功处理（与历史 `bool(True)=True` 一致）
            # - key 存在且为 None / 未知字符串：保守视作失败（default=False），
            #   保留原 `bool(None)=False` 语义，同时与 transport/http.py 判定同步
            normalized.setdefault(
                "ok",
                coerce_bool(normalized.get("status", True), default=False),
            )
            normalized.setdefault(
                "message",
                str(normalized.get("message") or normalized.get("error") or ""),
            )
            return cls.model_validate(normalized)
        return cls.model_validate(
            {
                "ok": coerce_bool(data, default=False),
                "message": ""
            }
        )
