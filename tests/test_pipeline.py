import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf
from rhythm.features import audio_features, labels, LABELS
from rhythm.pipeline import build
from rhythm.dataset import RhythmDataset

ROOT = Path(__file__).resolve().parents[1]


class FeatureTests(unittest.TestCase):
    def test_exact_frame_labels_and_hold_across_window(self):
        c = {'notes':[{'type':'tap','time':.5}, {'type':'tap','time':.5},
                      {'type':'hold','time':.98,'end_time':1.2},
                      {'type':'flick','time':1.0}]}
        y, onset, report = labels(c, 100, .02, 2)
        self.assertEqual(y[25, LABELS.index('tap')], 1)
        self.assertEqual(onset[50], 1)
        self.assertTrue((y[49:60, -1] == 1).all())
        self.assertEqual(y[60, -1], 0)
        self.assertEqual(report['merged_same_label_events'], 1)

    def test_out_of_range_not_clipped_into_first_frame(self):
        y, _, report = labels({'notes':[{'type':'tap','time':-.1}, {'type':'flick','time':2}]},100,.02,2)
        self.assertEqual(y.sum(), 0)
        self.assertEqual(len(report['out_of_audio']), 2)

    def test_centered_audio_and_resampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'pulse.wav'
            sr = 44100
            y = np.zeros(sr, dtype='float32')
            y[sr//2] = 1
            sf.write(path, y, sr)
            x, duration = audio_features(path)
            self.assertEqual(x.shape, (50,80))
            self.assertEqual(int(x.sum(axis=1).argmax()), 25)
            self.assertAlmostEqual(duration, 1)

    def test_batch_dataset_padding_error_reporting_and_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'in'; folder.mkdir()
            sf.write(folder/'demo.wav', np.zeros(22050*5, dtype='float32'), 22050)
            for name in ('mania.osu','sekai.sus'):
                (folder/name).write_bytes((ROOT/'examples'/name).read_bytes())
            (folder/'bad.sus').write_text('#BPM01: 0', encoding='utf-8')
            out = Path(tmp)/'out'
            report = build(folder, out, ROOT/'examples/manifest.json', True, window_seconds=2)
            self.assertEqual(report['parsed'], 2)
            self.assertEqual(report['failed'], 1)
            self.assertEqual(report['feature_windows'], 6)
            ds = RhythmDataset(out, 'all')
            self.assertEqual(len(ds), 6)
            self.assertEqual(ds[0]['x'].shape, (100,80))
            self.assertEqual(ds[2]['mask'].sum(), 50)
            self.assertTrue((ds[2]['y'][50:]==0).all())
            rows = [json.loads(s) for s in (out/'dataset.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(rows[0]['song_group'], rows[1]['song_group'])
            self.assertEqual(rows[0]['split'], rows[1]['split'])
            with self.assertRaises(ValueError):
                build(folder, out)

    def test_missing_audio_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'mania.osu'
            p.write_bytes((ROOT/'examples/mania.osu').read_bytes())
            report = build(p, Path(tmp)/'out', features=True)
            self.assertEqual(report['parsed'], 1)
            self.assertEqual(report['errors'][0]['stage'], 'features')


if __name__ == '__main__':
    unittest.main()
