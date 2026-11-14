import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from voice_control.intent import CATALOG, parse_command  # noqa: E402


class CommandTest(unittest.TestCase):
    def test_every_catalog_phrase_maps_to_its_action(self):
        for row in CATALOG:
            for phrase in row['examples']:
                with self.subTest(phrase=phrase):
                    result = parse_command(phrase)
                    self.assertTrue(result['recognized'], result)
                    self.assertEqual(result['action'], row['action'], result)

    def test_song_artist_and_platform(self):
        cases = [
            ('播放晴天', 'netease', '晴天', None),
            ('请帮我播放周杰伦的晴天。', 'netease', '晴天', '周杰伦'),
            ('我想听周杰伦唱的晴天', 'netease', '晴天', '周杰伦'),
            ('播放陈奕迅演唱的富士山下', 'netease', '富士山下', '陈奕迅'),
            ('播放歌手张三的歌曲夜曲', 'netease', '夜曲', '张三'),
            ('播放晴天，周杰伦唱的', 'netease', '晴天', '周杰伦'),
            ('播放Taylor Swift的Love Story', 'netease', 'Love Story', 'Taylor Swift'),
            ('" Play Love Story by Taylor Swift ".', 'netease', 'Love Story', 'Taylor Swift'),
            ('播放周杰伦的《晴天》', 'netease', '晴天', '周杰伦'),
            ('播放张三的《夜曲》', 'netease', '夜曲', '张三'),
            ('B站播放周杰伦现场', 'bilibili', '周杰伦现场', None),
            ('在B站搜周杰伦现场', 'bilibili', '周杰伦现场', None),
            ('播放B站的周杰伦现场', 'bilibili', '周杰伦现场', None),
            ('播放《暂停》', 'netease', '暂停', None),
            ('播放"下一首"', 'netease', '下一首', None),
            ('播放不要说话', 'netease', '不要说话', None),
            ('播放《先暂停，然后播放》', 'netease', '先暂停，然后播放', None),
        ]
        for phrase, platform, song, artist in cases:
            with self.subTest(phrase=phrase):
                result = parse_command(phrase)
                self.assertEqual(result['action'], 'play', result)
                self.assertEqual(result['parameters']['platform'], platform)
                self.assertEqual(result['parameters']['song'], song)
                self.assertEqual(result['parameters']['artist'], artist)
                self.assertEqual(result['label'], '搜索播放')

    def test_titles_with_de_are_not_silently_split(self):
        for phrase, song in [('播放最美的太阳', '最美的太阳'), ('播放我的天空', '我的天空'),
                             ('播放张三的夜曲', '张三的夜曲')]:
            result = parse_command(phrase)
            self.assertEqual(result['parameters']['song'], song)
            self.assertIsNone(result['parameters']['artist'])
            self.assertTrue(result['needs_confirmation'])
        self.assertFalse(parse_command('播放《最美的太阳》')['needs_confirmation'])

    def test_volume_numbers_and_limits(self):
        for phrase, value in [('音量调到五十', 50), ('音量百分之五十', 50), ('音量50%', 50),
                              ('音量调到一百', 100), ('声音调到一半', 50), ('音量设为零', 0),
                              ('音量设为九十九', 99), ('音量调到１０', 10), ('音量调至十', 10)]:
            with self.subTest(phrase=phrase):
                result = parse_command(phrase)
                self.assertEqual(result['action'], 'volume_set', result)
                self.assertEqual(result['parameters']['value'], value)
        for phrase in ('音量调到101', '音量调到-1', '音量调到1.5', '音量调到五百', '音量调到最大'):
            self.assertFalse(parse_command(phrase)['recognized'], phrase)
        for phrase, delta in [('声音大一点', 10), ('声音小一点', -10), ('把音量调小一点', -10)]:
            self.assertEqual(parse_command(phrase)['parameters']['delta'], delta)

    def test_random_likes_and_shuffle_are_distinct(self):
        self.assertEqual(parse_command('随机来一首')['action'], 'random_liked')
        self.assertEqual(parse_command('随机播放队列')['parameters'], {'mode': 'shuffle'})
        self.assertFalse(parse_command('随机播放')['recognized'])

    def test_negative_multiple_and_unknown_do_not_dispatch(self):
        for phrase in ('不要播放晴天', '不是播放晴天', '不要暂停', '暂停并且下一首',
                       '播放晴天然后下一首', '暂停，播放晴天', '播放晴天，别放了',
                       '播放晴天和暂停', '今天的天气很好', '播放', '播放音乐', '', None, 'x'*501):
            with self.subTest(phrase=phrase):
                result = parse_command(phrase)
                self.assertFalse(result['recognized'], result)
                self.assertIsNone(result['action'])
        self.assertEqual(parse_command('别放了')['action'], 'stop')
        self.assertEqual(parse_command('取消')['action'], 'cancel')
        self.assertNotEqual(parse_command('取消')['action'], parse_command('停止播放')['action'])

    def test_nevermind_cancels_only_the_voice_request(self):
        for phrase in ('没事', '没事了', '没事。', '算了', '不用了'):
            self.assertEqual(parse_command(phrase)['action'], 'cancel')
        self.assertEqual(parse_command('播放没事')['action'], 'play')


if __name__ == '__main__':
    unittest.main()
