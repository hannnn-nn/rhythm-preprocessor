"""Strict, dependency-free parsers. All exported times are AUDIO seconds."""
from bisect import bisect_right
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import re


class ChartError(ValueError):
    pass


def number(value):
    n = float(value)
    if not math.isfinite(n):
        raise ChartError(f'Non-finite number: {value}')
    return n


def event(t, lane, width, keys, kind, **extra):
    return dict(time=round(t, 9), lane=lane, width=width,
                position=(lane + width / 2) / keys,
                width_normalized=width / keys, type=kind, **extra)


def parse_osu(text):
    sections, section = {}, ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        else:
            sections.setdefault(section, []).append(line)
    def props(name):
        return dict((k.strip(), v.strip()) for k, v in
                    (s.split(':', 1) for s in sections.get(name, []) if ':' in s))
    general, diff, meta = props('General'), props('Difficulty'), props('Metadata')
    if general.get('Mode') != '3':
        raise ChartError('Only native osu!mania Mode:3 is supported (no auto-convert).')
    key_float = number(diff.get('CircleSize', 0))
    keys = int(key_float)
    if keys != key_float or not 1 <= keys <= 18:
        raise ChartError('Invalid mania key count')
    timing, scroll = [], []
    for line in sections.get('TimingPoints', []):
        p = line.split(',')
        if len(p) < 2:
            raise ChartError(f'Invalid timing point: {line}')
        t, length = number(p[0]) / 1000, number(p[1])
        inherited = len(p) >= 7 and p[6].strip() == '0'
        if inherited:
            if length >= 0:
                raise ChartError('Inherited beat length must be negative')
            scroll.append(dict(time=t, multiplier=-100 / length))
        else:
            if length <= 0:
                raise ChartError('BPM beat length must be positive')
            timing.append(dict(time=t, bpm=60000 / length,
                               meter=int(p[2]) if len(p) > 2 else 4))
    timing.sort(key=lambda x: x['time'])
    if not timing:
        raise ChartError('Missing BPM timing points')
    timing[0]['beat'] = 0.0
    for prev, cur in zip(timing, timing[1:]):
        cur['beat'] = prev['beat'] + (cur['time'] - prev['time']) * prev['bpm'] / 60
    def beat(t):
        tp = timing[max(0, bisect_right([x['time'] for x in timing], t) - 1)]
        return tp['beat'] + (t - tp['time']) * tp['bpm'] / 60
    notes = []
    for line in sections.get('HitObjects', []):
        p = line.split(',')
        if len(p) < 5:
            raise ChartError(f'Invalid hit object: {line}')
        x, t, flags = number(p[0]), number(p[2]) / 1000, int(p[3])
        lane = min(keys - 1, max(0, math.floor(x * keys / 512)))
        if flags & 128:
            if len(p) < 6:
                raise ChartError('Hold missing end time')
            end = number(p[5].split(':')[0]) / 1000
            if end <= t:
                raise ChartError('Hold end must be after start')
            notes.append(event(t, lane, 1, keys, 'hold', end_time=end,
                               duration=end-t, beat=beat(t), end_beat=beat(end)))
        elif flags & 1:
            notes.append(event(t, lane, 1, keys, 'tap', beat=beat(t)))
        else:
            raise ChartError(f'Unsupported mania object type: {flags}')
    return dict(source_game='osu_mania', key_count=keys,
                metadata=meta, difficulty=meta.get('Version'),
                audio_filename=general.get('AudioFilename'), timing=timing,
                scroll=scroll, notes=notes, warnings=[])


def parse_sus(text):
    """Project Sekai SUS profile: playable lanes 2..13 -> 0..11.

    Preserve slide control nodes; do not pretend that visual guide/easing
    extensions can be converted to accurate key-state labels.
    """
    meta, bpm_defs, measures, rows, visual, warnings = {}, {}, {}, [], [], []
    base = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line.startswith('#'):
            continue
        match = re.fullmatch(r'#(\d{3})([0-9a-zA-Z]{2,3}):\s*(.*)', line)
        if match:
            m, channel, data = match.groups()
            m = int(m) + base
            if m < 0 or m > 100000:
                raise ChartError('Measure number outside supported range')
            if channel == '02':
                measures[m] = Fraction(data.strip())
                if measures[m] <= 0:
                    raise ChartError('Measure length must be positive')
            else:
                rows.append((m, channel.lower(), re.sub(r'\s', '', data), lineno))
            continue
        match = re.fullmatch(r'#BPM([0-9a-zA-Z]{2}):\s*(\S+)', line, re.I)
        if match:
            ident, value = match.groups()
            bpm_defs[ident.lower()] = number(value)
            if bpm_defs[ident.lower()] <= 0:
                raise ChartError('BPM must be positive')
            continue
        if line.upper().startswith(('#TIL', '#HISPEED', '#NOSPEED', '#MEASUREHS',
                                    '#ATR', '#ATTRIBUTE', '#NOATTRIBUTE')):
            visual.append(line)
            continue
        match = re.fullmatch(r'#(\w+)\s+(.+)', line)
        if not match:
            raise ChartError(f'Unrecognized SUS line {lineno}: {line}')
        key, value = match.groups()
        key, value = key.upper(), value.strip().strip('"')
        if key == 'MEASUREBS':
            base = int(value)
        elif key == 'REQUEST':
            if not re.fullmatch(r'ticks_per_beat\s+[1-9]\d*', value):
                raise ChartError(f'Unsupported SUS request: {value}')
            meta['ticks_per_beat'] = int(value.split()[1])
        else:
            meta[key] = value
    max_measure = max([r[0] for r in rows] + list(measures) + [0])
    starts, lengths, length = [], [], Fraction(4)
    total = Fraction(0)
    for m in range(max_measure + 1):
        length = measures.get(m, length)
        starts.append(total)
        lengths.append(length)
        total += length
    tempos, raw_notes, controls = {}, [], []
    for m, channel, data, lineno in rows:
        if not data or len(data) % 2 or not re.fullmatch('[0-9a-zA-Z]+', data):
            raise ChartError(f'Invalid two-character cells at line {lineno}')
        count = len(data) // 2
        for i in range(count):
            token = data[i*2:i*2+2].lower()
            if token == '00':
                continue
            b = starts[m] + lengths[m] * Fraction(i, count)
            if channel == '08':
                if token not in bpm_defs:
                    raise ChartError(f'Undefined BPM {token}')
                if b in tempos and tempos[b] != bpm_defs[token]:
                    raise ChartError('Conflicting BPMs at same beat')
                tempos[b] = bpm_defs[token]
                continue
            family = channel[0]
            if family not in '1359' or len(channel) != (3 if family in '39' else 2):
                raise ChartError(f'Unsupported SUS channel: {channel}')
            lane, kind, width = int(channel[1], 36), int(token[0], 36), int(token[1], 36)
            if width == 0:
                raise ChartError('Zero width note')
            n = dict(b=b, lane=lane-2, width=width, family=family,
                     code=kind, channel=channel[2:] or None, line=lineno)
            if not 2 <= lane <= 13:
                controls.append(dict(beat=float(b), raw_lane=lane, code=kind, family=family))
                continue
            if lane + width > 14:
                raise ChartError('Note extends past playable lane 13')
            raw_notes.append(n)
    if Fraction(0) not in tempos:
        raise ChartError('SUS requires a BPM event at beat zero; no default guessed')
    offset = number(meta.get('WAVEOFFSET', 0))
    beats = sorted(tempos)
    times = [-offset]
    for previous, current in zip(beats, beats[1:]):
        times.append(times[-1] + float(current-previous) * 60 / tempos[previous])
    def seconds(b):
        j = bisect_right(beats, b)-1
        return times[j] + float(b-beats[j])*60/tempos[beats[j]]
    timing = [dict(beat=float(b), time=t, bpm=tempos[b]) for b, t in zip(beats, times)]
    taps, directions, chains, guides = {}, {}, {}, []
    for n in raw_notes:
        key = (n['b'], n['lane'])
        if n['family'] == '1':
            if n['code'] not in (1, 2, 3, 5, 6):
                raise ChartError(f'Unsupported tap/hidden-end extension {n["code"]} at line {n["line"]}')
            if key in taps:
                raise ChartError('Duplicate tap at same beat/lane')
            taps[key] = n
        elif n['family'] == '5':
            if n['code'] not in (1, 3, 4):
                raise ChartError(f'Unsupported Sekai flick direction {n["code"]}')
            if key in directions:
                raise ChartError('Duplicate directional marker')
            directions[key] = n
        elif n['family'] == '9':
            guides.append(dict(beat=float(n['b']), lane=n['lane'], width=n['width'],
                               code=n['code'], channel=n['channel']))
        else:
            if n['code'] not in (1, 2, 3, 4, 5):
                raise ChartError(f'Unsupported slide node {n["code"]}')
            chains.setdefault(n['channel'], []).append(n)
    for n in raw_notes:
        if n['family'] == '9' and ((n['b'], n['lane']) in taps or (n['b'], n['lane']) in directions):
            raise ChartError('Guide with overlapping gameplay markers needs explicit extension support')
    def decorated(n, node=False):
        key = (n['b'], n['lane'])
        marker = taps.pop(key, None) if node else n
        direction = directions.pop(key, None)
        code = marker['code'] if marker else 1
        flick = direction is not None or code == 3
        out = event(seconds(n['b']), n['lane'], n['width'], 12,
                    'flick' if flick else ('trace' if code in (5, 6) else 'tap'),
                    beat=float(n['b']), critical=code in (2, 6))
        if flick:
            out['direction'] = {1:'up', 3:'up_left', 4:'up_right'}.get(
                direction['code'] if direction else 1)
        return out
    notes = []
    for channel, nodes in chains.items():
        active = None
        for n in sorted(nodes, key=lambda v: v['b']):
            code = n['code']
            if code == 1:
                if active is not None:
                    raise ChartError(f'Overlapping slides on channel {channel}')
                active = []
            elif active is None:
                raise ChartError(f'Orphan slide node on channel {channel}')
            p = decorated(n, node=True)
            p['role'] = {1:'start', 2:'end', 3:'visible', 4:'control', 5:'invisible'}[code]
            active.append(p)
            if code == 2:
                if active[-1]['time'] <= active[0]['time']:
                    raise ChartError('Slide must have positive duration')
                head, tail = active[0], active[-1]
                notes.append(dict(head, type='slide', end_time=tail['time'],
                                  duration=tail['time']-head['time'],
                                  end_beat=tail['beat'], path=active, channel=channel))
                active = None
        if active is not None:
            raise ChartError(f'Unclosed slide on channel {channel}')
    for n in list(taps.values()):
        notes.append(decorated(n))
    if directions:
        raise ChartError('Flick direction without corresponding tap/slide node')
    if guides:
        warnings.append('Decorative guide nodes retained separately; excluded from training targets.')
    if visual:
        warnings.append('Visual scroll/attribute commands preserved as raw text; not rendered.')
    return dict(source_game='project_sekai', key_count=12, metadata=meta,
                difficulty=meta.get('DIFFICULTY'), audio_filename=meta.get('WAVE'),
                timing=timing, measure_lengths={str(k):float(v) for k,v in measures.items()},
                notes=notes, warnings=warnings, controls=controls, guides=guides,
                visual_commands=visual, wave_offset_seconds=offset)


def parse_file(path):
    path = Path(path)
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ChartError('Expected UTF-8 input; convert encoding explicitly first') from exc
    if path.suffix.lower() == '.osu':
        chart = parse_osu(text)
    elif path.suffix.lower() == '.sus':
        chart = parse_sus(text)
    else:
        raise ChartError('Supported inputs: .osu and .sus')
    if not chart['notes']:
        raise ChartError('Empty playable chart')
    chart['notes'].sort(key=lambda n: (n['time'], n['lane'], n['type']))
    chart.update(schema_version='1.0', chart_id=hashlib.sha256(raw).hexdigest(),
                 source_path=str(path.resolve()), time_unit='seconds',
                 time_origin='audio_start', lane_index_origin=0)
    return chart
