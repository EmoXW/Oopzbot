"""Present documented role fields without dumping unknown API properties."""


def clean(value):
    return str(value).replace("\r", " ").replace("\n", " ")[:80]


def number(value):
    text = str(value).strip()
    try:
        return f"{int(text):,}"
    except (TypeError, ValueError):
        return clean(value)


def role_text(data):
    if not isinstance(data, dict):
        return "三角洲角色信息\n当前暂无可展示的数据。"
    lines = ["三角洲角色信息"]
    fields = (
        (("name", "role_name", "nickname", "nick_name"), "角色名", False),
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
