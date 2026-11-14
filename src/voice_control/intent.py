"""Deterministic voice intents; execution is handled separately."""
import re
import unicodedata

ARTISTS = ('周杰伦', '陈奕迅', '林俊杰', '邓紫棋', '王菲', '张学友', '刘德华', '许嵩',
           '薛之谦', '孙燕姿', '陶喆', '蔡依林', '张惠妹', '五月天', '毛不易',
           'Taylor Swift', 'Adele', 'Bruno Mars', 'Lady Gaga', 'Coldplay', '周深')
CATALOG = [
    {'action': 'play', 'label': '搜索播放', 'examples': ['播放晴天', '放一首晴天', '我想听晴天'], 'effect': '搜索歌曲，找到匹配后播放；多个相近结果需确认。'},
    {'action': 'play', 'label': '指定歌手播放', 'examples': ['播放周杰伦的晴天', '我想听周杰伦唱的晴天', '播放Taylor Swift的Love Story'], 'effect': '同时按歌手和歌名搜索，避免选错版本。'},
    {'action': 'play', 'label': 'B站播放', 'examples': ['B站播放周杰伦现场', '在B站搜周杰伦现场'], 'effect': '使用 B站来源搜索播放。'},
    {'action': 'random_liked', 'label': '随机来一首', 'examples': ['随机来一首', '随便放首歌', '随机播放一首'], 'effect': '从网易云喜欢列表随机选一首，不改变队列模式。'},
    {'action': 'next', 'label': '下一首', 'examples': ['下一首', '切歌', '换一首', '跳过这首'], 'effect': '跳过当前歌曲，播放下一首。'},
    {'action': 'pause', 'label': '暂停', 'examples': ['暂停', '暂停音乐', '先暂停一下'], 'effect': '暂停当前歌曲。'},
    {'action': 'resume', 'label': '继续播放', 'examples': ['继续', '继续播放', '恢复播放'], 'effect': '恢复暂停的歌曲。'},
    {'action': 'stop', 'label': '停止播放', 'examples': ['停止播放', '关掉音乐', '别放了'], 'effect': '停止播放并退出语音频道。'},
    {'action': 'volume_adjust', 'label': '调大音量', 'examples': ['声音大一点', '调大音量'], 'effect': '音量增加 10，最高 100。'},
    {'action': 'volume_adjust', 'label': '调小音量', 'examples': ['声音小一点', '调小音量'], 'effect': '音量降低 10，最低 0。'},
    {'action': 'volume_set', 'label': '指定音量', 'examples': ['音量调到五十', '音量百分之五十'], 'effect': '音量设为指定数值，支持 0 到 100。'},
    {'action': 'mute', 'label': '静音', 'examples': ['静音', '把音乐静音'], 'effect': '保存当前音量，再设为 0。'},
    {'action': 'unmute', 'label': '取消静音', 'examples': ['取消静音', '恢复声音'], 'effect': '恢复静音前的音量。'},
    {'action': 'mode', 'label': '单曲循环', 'examples': ['单曲循环', '一直循环这首'], 'effect': '将播放模式设为单曲循环。'},
    {'action': 'mode', 'label': '顺序播放', 'examples': ['顺序播放', '按顺序播放', '列表播放'], 'effect': '将播放模式设为顺序播放。'},
    {'action': 'mode', 'label': '随机播放队列', 'examples': ['随机播放队列', '打乱播放顺序'], 'effect': '随机播放队列中的歌曲，不是从喜欢列表选歌。'},
    {'action': 'mode', 'label': '播完就停', 'examples': ['这首播完就停', '播完这首就停'], 'effect': '当前歌曲结束后停止播放。'},
    {'action': 'current', 'label': '查询当前歌曲', 'examples': ['现在什么歌', '这首叫什么', '谁唱的'], 'effect': '查询当前歌曲名和歌手。'},
    {'action': 'queue', 'label': '查看队列', 'examples': ['查看队列', '后面还有什么歌'], 'effect': '查询待播放歌曲列表。'},
    {'action': 'clear_queue', 'label': '清空队列', 'examples': ['清空队列', '删掉后面的歌'], 'effect': '清空待播放队列，保留当前歌曲。'},
    {'action': 'help', 'label': '命令帮助', 'examples': ['帮助', '你支持什么命令'], 'effect': '显示支持的语音说法和动作。'},
    {'action': 'cancel', 'label': '取消本次输入', 'examples': ['取消', '算了', '没事了'], 'effect': '结束本次语音输入，不改变音乐状态。'},
]
ALIASES = {
    'random_liked': ['随便来一首', '随机放一首', '随便播放一首', '随机一首'],
    'next': ['下一曲', '切下一首', '换首歌', '跳过', 'next', 'skip'],
    'pause': ['暂停一下', '暂停播放', '暂停这首歌', '暂停一下音乐', 'pause'],
    'resume': ['接着放', '接着播放', '继续放歌', 'resume', 'continue'],
    'stop': ['停止', '关闭音乐', '停止音乐', '别播了', '退出语音', 'stop', 'stop music'],
    'volume_adjust_up': ['把声音调大一点', '把音量调大一点', '声音调大一点', '音量大一点', '大声一点'],
    'volume_adjust_down': ['把声音调小一点', '把音量调小一点', '声音调小一点', '音量小一点', '小声一点'],
    'unmute': ['恢复音量', '解除静音', 'unmute'],
    'mute': ['音乐静音', 'mute'],
    'current': ['现在播放什么歌', '正在放什么歌', '这是什么歌', '这首歌叫什么', '这是谁唱的'],
    'queue': ['播放队列', '队列里有什么', '还有什么歌'],
    'clear_queue': ['清空播放队列', '清空后面的歌'],
    'help': ['语音帮助', '命令帮助', '有什么命令', 'help'],
    'cancel': ['取消输入', '取消本次输入', '不用了', 'cancel'],
}
_LABELS = {}
for _row in CATALOG:
    _LABELS.setdefault(_row['action'], _row['label'])
_LABELS.update(volume_adjust='调整音量', mode='切换播放模式')


def normalize(text):
    text = re.sub(r'[\uff21-\uff3a\uff41-\uff5a\uff10-\uff19]', lambda match: unicodedata.normalize('NFKC', match[0]), text).strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.strip(' \t\r\n，,。.!\uff01?\uff1f;；')
    for left, right in (('“', '”'), ('"', '"')):
        if text.startswith(left) and text.endswith(right):
            text = text[1:-1].strip()
    # Only strip polite prefixes; never strip words from the song's ending.
    for _ in range(3):
        match = re.match(r'^(?:麻烦你|麻烦|请你|请|帮我|给我)\s*', text)
        if not match:
            break
        text = text[match.end():].lstrip()
    return text


def key(text):
    return re.sub(r'\s+', '', text).casefold()


def number(text):
    text = text.strip().removeprefix('百分之').removesuffix('%').strip()
    if text == '一半':
        return 50
    if text.isascii() and text.isdigit():
        value = int(text)
    elif text in ('一百', '百'):
        value = 100
    else:
        digits = {'零': 0, '\u3007': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
                  '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
        if text in digits:
            value = digits[text]
        elif re.fullmatch('[一二三四五六七八九]?十[一二三四五六七八九]?', text):
            left, right = text.split('十')
            value = digits.get(left, 1) * 10 + digits.get(right, 0)
        else:
            return None
    return value if 0 <= value <= 100 else None


def preview(action, parameters=None, summary=None, confirmation=False, reason=''):
    return {'recognized': True, 'action': action,
            'label': _LABELS.get(action, action), 'parameters': parameters or {},
            'summary': summary or _LABELS.get(action, action),
            'needs_confirmation': confirmation, 'reason': reason}


def unknown(message):
    return {'recognized': False, 'action': None,
            'label': '请换一种说法', 'parameters': {}, 'summary': message,
            'needs_confirmation': False, 'reason': message}


def quoted(value):
    value = value.strip()
    for left, right in (('《', '》'), ('「', '」'), ('“', '”'), ('"', '"')):
        if value.startswith(left) and value.endswith(right):
            return value[1:-1].strip()
    return value


def parse_command(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 500:
        return unknown('请说一条不超过 500 字的音乐命令。')
    text = normalize(raw)
    compact = key(text)
    # Multiple commands are never silently reduced to the first one.
    # Quoted titles are excluded, so a title can itself contain command words.
    unquoted = re.sub(r'《[^》]*》|「[^」]*」|“[^”]*”|"[^"]*"', '歌名', text)
    if re.search(r'(?:然后|接着|并且|同时|和|[,，;；。])\s*(?:再|请|帮我)?\s*(?:播放|暂停|停止|切歌|下一首|调大|调小|清空|静音|别放了)', unquoted):
        return unknown('一次只接受一条命令，请分开说。')
    if compact == '随机播放':
        return unknown('请说“随机来一首”或“随机播放队列”，避免混淆选歌和播放模式。')
    special = {
        '声音大一点': ('volume_adjust', {'delta': 10, 'min': 0, 'max': 100}, '音量增加 10，最高 100'),
        '调大音量': ('volume_adjust', {'delta': 10, 'min': 0, 'max': 100}, '音量增加 10，最高 100'),
        '声音小一点': ('volume_adjust', {'delta': -10, 'min': 0, 'max': 100}, '音量降低 10，最低 0'),
        '调小音量': ('volume_adjust', {'delta': -10, 'min': 0, 'max': 100}, '音量降低 10，最低 0'),
        '单曲循环': ('mode', {'mode': 'single'}, '开启单曲循环'),
        '一直循环这首': ('mode', {'mode': 'single'}, '开启单曲循环'),
        '顺序播放': ('mode', {'mode': 'list'}, '按顺序播放队列'),
        '按顺序播放': ('mode', {'mode': 'list'}, '按顺序播放队列'),
        '列表播放': ('mode', {'mode': 'list'}, '按顺序播放队列'),
        '随机播放队列': ('mode', {'mode': 'shuffle'}, '随机播放队列中的歌曲'),
        '打乱播放顺序': ('mode', {'mode': 'shuffle'}, '随机播放队列中的歌曲'),
        '这首播完就停': ('mode', {'mode': 'stop'}, '当前歌曲播完后停止'),
        '播完这首就停': ('mode', {'mode': 'stop'}, '当前歌曲播完后停止'),
    }
    if compact in special:
        action, parameters, summary = special[compact]
        return preview(action, parameters, summary)
    for row in CATALOG:
        if row['action'] in ('play', 'volume_set', 'mode', 'volume_adjust'):
            continue
        if compact in {key(item) for item in row['examples'] + ALIASES.get(row['action'], [])}:
            params = {}
            if row['action'] in ('mute', 'unmute'):
                params = {'preserve_previous_volume': True}
            return preview(row['action'], params, row['effect'])
    for direction, delta in (('up', 10), ('down', -10)):
        if compact in {key(item) for item in ALIASES['volume_adjust_' + direction]}:
            return preview('volume_adjust', {'delta': delta, 'min': 0, 'max': 100},
                           f'音量{"增加" if delta > 0 else "降低"} 10，范围 0 到 100')
    volume = re.fullmatch(r'(?:把)?(?:音量|声音)(?:设置为|设置到|设为|调到|调成|调至|设成|到)?\s*(.+)', text)
    if volume:
        value = number(volume[1])
        if value is None:
            return unknown('音量请说 0 到 100，例如“音量调到五十”。')
        return preview('volume_set', {'value': value, 'min': 0, 'max': 100}, f'将音量设为 {value}')
    if re.match(r'^(?:不要|不用|不是|别|先别|暂时不)', text):
        return unknown('这是一条否定或不明确的指令，不执行动作。')

    platform = 'netease'
    phrase = text
    bstation = re.match(r'^(?:在)?(?:B\s*站|哔哩哔哩)(?:上)?\s*(?:播放|搜索|搜|放)?\s*(.*)$', text, re.I)
    if bstation:
        platform, phrase = 'bilibili', bstation[1].strip()
    else:
        play = re.match(r'^(?:我想听|想听|放一首|放首|来一首|来首|点播|播放|放一下|放个|放)\s*(.*)$', text)
        english = re.match(r'^play\s+(.+)$', text, re.I)
        if play:
            phrase = play[1].strip()
            source = re.match(r'^(?:B\s*站|哔哩哔哩)(?:的)?\s*(.*)$', phrase, re.I)
            if source:
                platform, phrase = 'bilibili', source[1].strip()
        elif english:
            phrase = english[1].strip()
        else:
            return unknown('没有识别到支持的命令。可以说“播放晴天”“下一首”或“帮助”。')
    if not phrase or phrase in ('音乐', '歌曲', '一首歌', '歌'):
        return unknown('请补充歌名或搜索关键词，例如“播放晴天”。')
    song, artist, ambiguous = quoted(phrase), None, False
    explicit_title = bool(re.fullmatch(r'《[^》]+》|「[^」]+」|“[^”]+”|"[^"]+"', phrase))
    if not explicit_title:
        by = re.fullmatch(r'(.+?)\s+by\s+(.+)', phrase, re.I)
        sung = re.fullmatch(r'(.+?)(?:演唱|唱)的\s*(.+)', phrase)
        explicit_singer = re.fullmatch(r'歌手\s*(.+?)的\s*(?:歌曲)?\s*(.+)', phrase)
        reverse = re.fullmatch(r'(.+?)[,，]\s*(.+?)(?:唱|演唱)的', phrase)
        if by:
            song, artist = quoted(by[1]), by[2].strip()
        elif sung or explicit_singer:
            match = sung or explicit_singer
            assert match is not None
            artist, song = match[1].strip(), quoted(match[2])
        elif reverse:
            song, artist = quoted(reverse[1]), reverse[2].strip()
        else:
            for name in sorted(ARTISTS, key=len, reverse=True):
                match = re.fullmatch(re.escape(name) + r'\s*的\s*(.+)', phrase, re.I)
                if match:
                    artist, song = name, quoted(match[1])
                    break
            if artist is None and '的' in phrase:
                match = re.fullmatch(r'(.{1,40}?)的\s*(《[^》]+》|「[^」]+」)', phrase)
                if match:
                    artist, song = match[1].strip(), quoted(match[2])
                else:
                    # '最美的太阳' may be a song title, not artist 最美 / song 太阳.
                    ambiguous = True
    if not song or (artist is not None and not artist):
        return unknown('请补充完整的歌名和歌手。')
    parameters = {'platform': platform, 'song': song, 'artist': artist,
                  'query': f'{artist} {song}' if artist else song,
                  'confirm_ambiguous_search_results': True}
    source = 'B站' if platform == 'bilibili' else '网易云'
    summary = f'在{source}搜索并播放：' + (f'{artist} · {song}' if artist else song)
    return preview('play', parameters, summary, ambiguous,
                   '“的”可能属于歌名，暂按完整关键词搜索；如需指定歌手，可说“播放某某唱的某首歌”。' if ambiguous else '')
