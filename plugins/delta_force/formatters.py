"""Present documented role fields without dumping unknown API properties."""


def clean(value):
    return str(value).replace("\r", " ").replace("\n", " ")[:80]


def number(value):
    text = str(value).strip()
    try:
        return f"{int(text):,}"
    except (TypeError, ValueError):
        return clean(value)


def game_nickname(data):
    if isinstance(data, dict):
        for key in ("name", "role_name", "nickname", "nick_name"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return clean(value.strip())
    return "暂未获取到游戏昵称"


def role_text(data):
    if not isinstance(data, dict):
        return "三角洲角色信息\n当前暂无可展示的数据。"
    lines = ["三角洲角色信息", f"角色名：{game_nickname(data)}"]
    fields = (
        (("level",), "等级", True),
        (("exp",), "当前经验", True),
        (("tdmLevel", "tdm_level"), "全面战场等级", True),
        (("tdmExp", "tdm_exp"), "全面战场经验", True),
        (("rank_name", "rank"), "段位", False),
        (("hafcoinnum", "haf_coin_num"), "哈夫币", True),
        (("propcapital", "prop_capital"), "物资价值", True),
        (("totalPrice", "total_price"), "总资产", True),
        (("currentAsset", "current_asset"), "流动资产", True),
        (("noncurrentAsset", "noncurrent_asset"), "固定资产", True),
    )
    for aliases, label, numeric in fields:
        value = next((data[key] for key in aliases if data.get(key) is not None), None)
        if value is not None:
            lines.append(f"{label}：{number(value) if numeric else clean(value)}")
    if len(lines) == 1:
        lines.append("当前暂无可展示的数据。")
    return "\n".join(lines)


def record_text(data, queue, nickname):
    """Use WeGame's queue-specific result enums, without exposing internal IDs."""
    if not isinstance(data, dict):
        return f"角色名：{nickname}\n当前暂无可展示的数据。"
    lines = [f"角色名：{nickname}"]
    result_key = "gameResult" if queue == "sol" else "result"
    results = {0: "撤离成功", 1: "撤离失败"} if queue == "sol" else {0: "失败", 1: "胜利", 2: "平局"}
    result = data.get(result_key)
    lines.append("模式：" + ("烽火地带" if queue == "sol" else "全面战场"))
    lines.append("结果：" + (results.get(result, "暂缺") if isinstance(result, int) else "暂缺"))
    for key in ("mapName", "map_name"):
        if data.get(key):
            lines.append(f"地图：{clean(data[key])}")
            break
    fields = (("gainedPrice", "带出价值"), ("ProfitLoss", "盈亏"),
              ("killPlayer", "击杀干员"), ("killCnt", "击杀总数"),
              ("killNum", "击杀"), ("death", "死亡"),
              ("assistCnt", "助攻"), ("assist", "助攻"), ("rescue", "救援"))
    for key, label in fields:
        if data.get(key) is not None:
            lines.append(f"{label}：{number(data[key])}")
    duration = data.get("gameTime")
    if isinstance(duration, (int, float)) and 0 <= duration <= 86400:
        minutes, seconds = divmod(int(duration), 60)
        lines.append(f"对局时长：{minutes}分{seconds:02d}秒")
    if data.get("dtEventTime"):
        lines.append(f"对局时间：{clean(data['dtEventTime'])}")
    return "\n".join(lines)
