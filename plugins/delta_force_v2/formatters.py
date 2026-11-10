"""Bounded text rendering with credential and private-account fields excluded."""
import re

SENSITIVE = re.compile(r"token|cookie|secret|password|api.?key|authorization|openid|unionid|qr|uin|client.?id|user.?identifier", re.I)
LABELS = {
    "nickName": "昵称", "nickname": "昵称", "name": "名称", "level": "等级",
    "rank": "段位", "mapName": "地图", "kill": "击杀", "kills": "击杀",
    "death": "死亡", "deaths": "死亡", "kd": "KD", "duration": "时长",
    "win": "胜利", "wins": "胜场", "total": "总计", "date": "日期",
}


def render(title, data):
    lines = [title]

    def walk(value, depth=0):
        if depth > 5 or len(lines) >= 65:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if SENSITIVE.search(str(key)):
                    continue
                label = LABELS.get(key, str(key))
                if isinstance(item, (list, dict)):
                    lines.append(f"{label}：")
                    walk(item, depth + 1)
                elif item is not None:
                    text = str(item)
                    if not text.startswith(("http:", "https:", "data:")) and "sk-" not in text:
                        lines.append(f"{label}：{text[:160]}")
        elif isinstance(value, list):
            for index, item in enumerate(value[:10], 1):
                lines.append(f"— {index} —")
                walk(item, depth + 1)

    walk(data)
    return "\n".join(lines)[:3200] if len(lines) > 1 else title + "\n暂无可展示的数据。"
