import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import unicodedata
from .parsers import parse_file


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', str(text)).casefold().split())


def resolve_audio(path, chart, entry):
    name = entry.get('audio') or chart.get('audio_filename')
    if not name:
        return None
    audio = Path(name)
    if not audio.is_absolute():
        audio = path.parent / audio
    return audio.resolve()


def assign_groups(items, seed):
    """Union every shared song ID, metadata title/artist, or exact audio hash."""
    parents = list(range(len(items)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    seen = {}
    item_keys = []
    for i, item in enumerate(items):
        c = item['chart']
        m = c['metadata']
        title = normalize(m.get('TitleUnicode') or m.get('Title') or m.get('TITLE') or '')
        artist = normalize(m.get('ArtistUnicode') or m.get('Artist') or m.get('ARTIST') or '')
        keys = []
        if item['entry'].get('song_id'):
            keys.append('id:' + str(item['entry']['song_id']))
        if item.get('audio_sha256'):
            keys.append('audio:' + item['audio_sha256'])
        if title and artist:
            keys.append('metadata:' + artist + ':' + title)
        if m.get('SONGID'):
            keys.append('sekai:' + str(m['SONGID']))
        if c['source_game'] == 'osu_mania' and m.get('BeatmapSetID') not in (None, '', '-1', '0'):
            keys.append('osu_set:' + m['BeatmapSetID'])
        if not keys:
            # Conservative fallback: all unidentifiable charts together.
            keys = ['unresolved_song_group']
            c['warnings'].append('No reliable song identity; grouped with all unresolved charts. Supply song_id.')
        for key in keys:
            if key in seen:
                parents[root(i)] = root(seen[key])
            seen[key] = i
        item_keys.append(keys)
    groups = {}
    for i, keys in enumerate(item_keys):
        groups.setdefault(root(i), set()).update(keys)
    for i, item in enumerate(items):
        group = hashlib.sha256('\n'.join(sorted(groups[root(i)])).encode()).hexdigest()
        bucket = int(hashlib.sha256((str(seed)+':'+group).encode()).hexdigest()[:8], 16)/2**32
        item.update(song_group=group, split='train' if bucket < .8 else 'validation' if bucket < .9 else 'test')


def build(source, output, manifest=None, features=False, seed=42, window_seconds=8):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.exists():
        raise ValueError(f'Input not found: {source}')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be a new or empty directory (prevents stale datasets).')
    if not math.isfinite(window_seconds) or window_seconds <= 0:
        raise ValueError('window_seconds must be finite and positive')
    config = json.loads(Path(manifest).read_text(encoding='utf-8-sig')) if manifest else {}
    entries = config.get('charts', {})
    paths = sorted(p for p in source.rglob('*') if p.suffix.lower() in ('.osu', '.sus')) if source.is_dir() else [source]
    if not paths:
        raise ValueError('No .osu or .sus files found. Extract .osz files first.')
    (output/'charts').mkdir(parents=True, exist_ok=True)
    if features:
        (output/'features').mkdir()
    items, errors, duplicates, ids = [], [], [], set()
    root_dir = source if source.is_dir() else source.parent
    for path in paths:
        relative = path.relative_to(root_dir).as_posix()
        try:
            chart = parse_file(path)
            if chart['chart_id'] in ids:
                duplicates.append(relative)
                continue
            entry = entries.get(relative, {})
            correction = float(entry.get('audio_time_shift_seconds', 0))
            if not math.isfinite(correction):
                raise ValueError('Invalid audio_time_shift_seconds')
            if correction:
                for n in chart['notes']:
                    n['time'] += correction
                    if 'end_time' in n:
                        n['end_time'] += correction
                    for node in n.get('path', []):
                        node['time'] += correction
                for tp in chart['timing']:
                    tp['time'] += correction
                for sp in chart.get('scroll', []):
                    sp['time'] += correction
            chart['audio_time_shift_seconds'] = correction
            chart['provenance'] = {k:entry.get(k, 'unknown') for k in ('source_url','license','song_id')}
            if 'difficulty' in entry:
                chart['difficulty'] = entry['difficulty']
            audio = resolve_audio(path, chart, entry)
            chart['audio_path'] = str(audio) if audio else None
            item = dict(chart=chart, entry=entry, relative=relative, audio=audio)
            if audio and audio.is_file():
                item['audio_sha256'] = digest(audio)
            else:
                chart['warnings'].append('Audio missing: chart JSON available; audio training features unavailable.')
            ids.add(chart['chart_id'])
            items.append(item)
        except (ValueError, OSError, KeyError, IndexError) as exc:
            errors.append(dict(source=relative, stage='parse', error=str(exc)))
    assign_groups(items, seed)
    index = []
    for item in items:
        c = item['chart']
        c.update(song_group=item['song_group'], split=item['split'])
        save(output/'charts'/f'{c["chart_id"]}.json', c)
        row = dict(chart_id=c['chart_id'], source=item['relative'], source_game=c['source_game'],
                   song_group=item['song_group'], split=item['split'],
                   chart=f'charts/{c["chart_id"]}.json', audio_path=c['audio_path'],
                   difficulty=c.get('difficulty'), key_count=c['key_count'],
                   note_count=len(c['notes']), warnings=c['warnings'], feature_files=[])
        if features:
            try:
                if not item['audio'] or not item['audio'].is_file():
                    raise ValueError('Audio missing; specify audio in manifest')
                from .features import make_features
                names, report = make_features(c, item['audio'], output/'features', window_seconds)
                row['feature_files'] = ['features/'+name for name in names]
                row['label_report'] = report
            except (ValueError, OSError, ImportError, RuntimeError) as exc:
                errors.append(dict(source=item['relative'], stage='features', error=str(exc)))
        index.append(row)
    with (output/'dataset.jsonl').open('w', encoding='utf-8') as f:
        for row in index:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
    split_counts = {s:len({r['song_group'] for r in index if r['split']==s}) for s in ('train','validation','test')}
    report = dict(parsed=len(index), failed=len(errors), duplicates=duplicates, errors=errors,
                  feature_windows=sum(len(r['feature_files']) for r in index),
                  song_groups_by_split=split_counts,
                  split_note='Hash-based approximate 80/10/10 by song group; small datasets may have empty splits.',
                  grouping_note='Different encodings/edits and inconsistent metadata need the same manifest song_id.',
                  seed=seed, window_seconds=window_seconds, schema_version='1.0')
    save(output/'report.json', report)
    return report


def godot(chart, lanes=4):
    if lanes < 1 or lanes > 32:
        raise ValueError('lanes must be 1..32')
    notes = []
    def lane(n):
        return min(lanes-1, max(0, math.floor(n['position']*lanes)))
    for n in chart['notes']:
        kind = n['type']
        if kind == 'trace':
            continue
        item = dict(time=n['time'], lane=lane(n), type='air' if kind=='flick' else 'hold' if kind=='slide' else kind)
        if kind in ('hold', 'slide'):
            item['duration'] = n['duration']
        notes.append(item)
        if kind == 'slide':
            for p in n['path']:
                if p['type'] == 'flick':
                    notes.append(dict(time=p['time'], lane=lane(p), type='air'))
    notes.sort(key=lambda n:(n['time'], n['lane'], n['type']))
    warnings = ['Preview mapping: slides become fixed-lane holds; trace notes omitted. Not a playability guarantee.']
    for i, a in enumerate(notes):
        for b in notes[i+1:]:
            if b['time'] > a['time'] + a.get('duration', 0):
                break
            if a['lane'] == b['lane']:
                warnings.append('Lane collision after reduction; review before gameplay.')
                break
    return dict(schema_version='godot-preview-1', lane_count=lanes, time_unit='seconds',
                notes=notes, warnings=sorted(set(warnings)))


def main():
    parser = argparse.ArgumentParser(description='osu!mania / Project Sekai SUS training preprocessor')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('parse', help='Parse one chart; Python standard library only')
    p.add_argument('input'); p.add_argument('output')
    p = sub.add_parser('build', help='Batch parse and optionally create audio training windows')
    p.add_argument('input'); p.add_argument('output')
    p.add_argument('--manifest'); p.add_argument('--features', action='store_true')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--window-seconds', type=float, default=8)
    p = sub.add_parser('godot', help='Export a deliberately simplified gameplay preview')
    p.add_argument('input'); p.add_argument('output'); p.add_argument('--lanes', type=int, default=4)
    args = parser.parse_args()
    try:
        if args.command in ('parse', 'godot'):
            target = Path(args.output)
            if target.exists():
                raise ValueError('Output already exists; choose a new path')
            if args.command == 'parse':
                result = parse_file(args.input)
            else:
                result = godot(json.loads(Path(args.input).read_text(encoding='utf-8')), args.lanes)
            target.parent.mkdir(parents=True, exist_ok=True)
            save(target, result)
            print(f'Saved: {target.resolve()}')
        else:
            result = build(args.input, args.output, args.manifest, args.features, args.seed, args.window_seconds)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result['failed'] else 0
    except (ValueError, OSError, ImportError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    return 0
