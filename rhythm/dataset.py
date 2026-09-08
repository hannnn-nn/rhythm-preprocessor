"""NumPy sequence dataset; torch DataLoader can wrap this directly."""
import json
from pathlib import Path
import numpy as np


class RhythmDataset:
    def __init__(self, root, split='train'):
        if split not in ('train', 'validation', 'test', 'all'):
            raise ValueError('Invalid split')
        self.root = Path(root)
        self.samples = []
        for line in (self.root/'dataset.jsonl').read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if split == 'all' or row['split'] == split:
                for path in row['feature_files']:
                    self.samples.append((path, row))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        path, row = self.samples[index]
        with np.load(self.root/path, allow_pickle=False) as data:
            return dict(x=data['x'].copy(), y=data['y'].copy(),
                        onset=data['onset'].copy(), mask=data['mask'].copy(),
                        chart_id=row['chart_id'], source_game=row['source_game'],
                        key_count=row['key_count'], difficulty=str(row['difficulty'] or 'unknown'))
