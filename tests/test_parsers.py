import unittest
from pathlib import Path
from rhythm.parsers import parse_osu, parse_sus, parse_file, ChartError
from rhythm.pipeline import assign_groups, godot

ROOT = Path(__file__).resolve().parents[1]


class ManiaTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT/'examples/mania.osu').read_text(encoding='utf-8')

    def test_lane_boundary_hold_and_bpm(self):
        c = parse_osu(self.text)
        self.assertEqual(c['notes'][-1]['lane'], 3)
        self.assertAlmostEqual(c['notes'][1]['duration'], .75)
        self.assertEqual(len(c['timing']), 2)
        self.assertEqual(c['scroll'][0]['multiplier'], 2)
        self.assertEqual(c['notes'][-1]['beat'], 6)
        self.assertEqual(c['timing'][-1]['bpm'], 240)

    def test_wrong_mode_and_invalid_hold(self):
        for text in (self.text.replace('Mode: 3','Mode: 0'),
                     self.text.replace('1750:', '900:'),
                     self.text.replace('CircleSize: 4', 'CircleSize: 4.5')):
            with self.assertRaises(ChartError):
                parse_osu(text)


class SekaiTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT/'examples/sekai.sus').read_text(encoding='utf-8')

    def test_tap_flick_slide_and_tempo_change(self):
        c = parse_sus(self.text)
        self.assertEqual(len(c['notes']), 3)
        slide = next(n for n in c['notes'] if n['type']=='slide')
        self.assertEqual(slide['time'], 2)
        self.assertEqual(slide['end_time'], 2.5)
        self.assertEqual(slide['path'][-1]['direction'], 'up_right')
        flick = next(n for n in c['notes'] if n['type']=='flick')
        self.assertTrue(flick['critical'])
        self.assertEqual(flick['lane'], 4)
        self.assertEqual(flick['direction'], 'up_left')

    def test_offset_sign_and_variable_measure(self):
        c = parse_sus('#WAVEOFFSET 0.25\n#BPM01: 120\n#00008: 01\n#00002: 3\n#00112: 11')
        self.assertEqual(c['notes'][0]['time'], 1.25)
        c = parse_sus('#WAVEOFFSET -0.25\n#BPM01: 120\n#00008: 01\n#00012: 11')
        self.assertEqual(c['notes'][0]['time'], .25)

    def test_mid_measure_tempo_fraction_and_base(self):
        c = parse_sus('#BPM01: 120\n#BPM02: 240\n#00008: 0102\n#00012: 00000011')
        self.assertEqual(c['notes'][0]['time'], 1.25)
        c = parse_sus('#BPM01: 120\n#00008: 01\n#MEASUREBS 1000\n#00012: 11')
        self.assertEqual(c['notes'][0]['time'], 2000)

    def test_channel_reuse_and_non_playable_controls(self):
        c = parse_sus(self.text+'\n#00232a: 12002200\n#00010: 41\n#0001f: 11')
        self.assertEqual(len([n for n in c['notes'] if n['type']=='slide']), 2)
        self.assertEqual(len(c['controls']), 2)

    def test_overlay_different_width(self):
        c = parse_sus(self.text.replace('#00056: 00003200','#00056: 00003100'))
        flick = next(n for n in c['notes'] if n['type']=='flick')
        self.assertEqual(flick['width'], 2)

    def test_malformed_or_unsupported_rejected(self):
        texts = [self.text.replace('#BPM01: 120','#BPM01: 0'),
                 self.text.replace('#00008: 01','#00008: zz'),
                 self.text.replace('#00138a: 00002200',''),
                 self.text+'\n#00212: 1', self.text+'\n#00212: 71',
                 self.text+'\n#00252: 11', self.text+'\n#0021d: 13',
                 self.text+'\n#00242a: 11', self.text+'\n#REQUEST "unknown 1"',
                 self.text+'\n#00292a: 11\n#00212: 21']
        for text in texts:
            with self.subTest(text=text[-45:]), self.assertRaises(ChartError):
                parse_sus(text)


class GroupTests(unittest.TestCase):
    def test_transitive_audio_and_song_identity(self):
        items = []
        for song, audio in [('a','hash1'), ('a','hash2'), ('b','hash2')]:
            c = parse_file(ROOT/'examples/mania.osu')
            c['metadata'] = {}
            items.append(dict(chart=c, entry={'song_id':song}, audio_sha256=audio))
        assign_groups(items, 42)
        self.assertEqual(len({i['song_group'] for i in items}), 1)
        self.assertEqual(len({i['split'] for i in items}), 1)

    def test_godot_tail_air_and_collision_warning(self):
        c = parse_file(ROOT/'examples/sekai.sus')
        out = godot(c, 4)
        self.assertTrue(any(n['type']=='air' and n['time']==2.5 for n in out['notes']))
        self.assertTrue(any(n['type']=='hold' for n in out['notes']))


if __name__ == '__main__':
    unittest.main()
