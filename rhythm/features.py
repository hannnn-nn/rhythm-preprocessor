"""Offline centered log-Mel features and frame-aligned multilabel targets."""
import math
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

LABELS = ['tap', 'hold_start', 'hold_end', 'air', 'trace', 'slide_tick', 'hold_active']


def audio_features(path, sample_rate=22050, hop=441, n_fft=2048, n_mels=80):
    y, original_sr = sf.read(str(path), dtype='float32', always_2d=True)
    y = y.mean(axis=1)
    if not len(y) or not np.isfinite(y).all():
        raise ValueError('Empty or non-finite audio')
    if original_sr != sample_rate:
        gcd = math.gcd(original_sr, sample_rate)
        y = resample_poly(y, sample_rate//gcd, original_sr//gcd).astype('float32')
    # Center each FFT window at k*hop; frame zero is audio time zero.
    frame_count = (len(y)-1)//hop + 1
    padded = np.pad(y, (n_fft//2, n_fft//2))
    frames = np.lib.stride_tricks.sliding_window_view(padded, n_fft)[::hop][:frame_count]
    hz_to_mel = lambda f: 2595 * np.log10(1 + f/700)
    edges = 700 * (10 ** (np.linspace(0, hz_to_mel(sample_rate/2), n_mels+2)/2595) - 1)
    freqs = np.fft.rfftfreq(n_fft, 1/sample_rate)
    bank = np.maximum(0, np.minimum((freqs[None,:]-edges[:-2,None]) /
                     (edges[1:-1,None]-edges[:-2,None]),
                     (edges[2:,None]-freqs[None,:])/(edges[2:,None]-edges[1:-1,None])))
    bank *= (2 / (edges[2:]-edges[:-2]))[:,None]
    mel = np.empty((frame_count, n_mels), dtype='float32')
    window = np.hanning(n_fft)
    for start in range(0, frame_count, 2048):
        power = abs(np.fft.rfft(frames[start:start+2048]*window, axis=1))**2
        mel[start:start+len(power)] = np.log1p(power @ bank.T)
    return mel, len(y)/sample_rate


def labels(chart, frame_count, dt, audio_duration):
    y = np.zeros((frame_count, len(LABELS)), dtype='float32')
    counts = np.zeros_like(y, dtype='int32')
    out_of_audio = []
    def pulse(time, kind):
        if time < 0 or time >= audio_duration:
            out_of_audio.append(dict(time=time, label=kind))
            return
        frame = min(frame_count-1, int(math.floor(time/dt + .5)))
        col = LABELS.index(kind)
        y[frame, col] = 1
        counts[frame, col] += 1
    for n in chart['notes']:
        kind, t = n['type'], n['time']
        if kind in ('hold', 'slide'):
            pulse(t, 'hold_start')
            pulse(n['end_time'], 'hold_end')
            start = max(0, math.ceil(t/dt))
            end = min(frame_count, math.ceil(n['end_time']/dt))
            if end > start:
                y[start:end, LABELS.index('hold_active')] = 1
            if kind == 'slide':
                for p in n['path']:
                    if p['type'] == 'flick':
                        pulse(p['time'], 'air')
                    if p['role'] == 'visible':
                        pulse(p['time'], 'slide_tick')
        else:
            pulse(t, 'air' if kind == 'flick' else kind)
    # Phase 1 timing task: fresh touch/hold/air, excluding release/trace/ticks.
    onset = y[:, [0, 1, 3]].max(axis=1)
    collisions = int(np.maximum(counts-1, 0).sum())
    return y, onset, dict(out_of_audio=out_of_audio,
                         merged_same_label_events=collisions)


def make_features(chart, audio, destination, window_seconds=8, sample_rate=22050,
                  hop=441):
    x, duration = audio_features(audio, sample_rate=sample_rate, hop=hop)
    dt = hop/sample_rate
    y, onset, report = labels(chart, len(x), dt, duration)
    if report['out_of_audio']:
        raise ValueError(f'{len(report["out_of_audio"])} label events outside audio; fix offset/audio pairing')
    frames = max(1, round(window_seconds/dt))
    paths = []
    for start in range(0, len(x), frames):
        valid = min(frames, len(x)-start)
        mask = np.arange(frames) < valid
        path = Path(destination) / f'{chart["chart_id"]}_{start:08d}.npz'
        np.savez_compressed(path,
            x=np.pad(x[start:start+valid], ((0, frames-valid), (0, 0))),
            y=np.pad(y[start:start+valid], ((0, frames-valid), (0, 0))),
            onset=np.pad(onset[start:start+valid], (0, frames-valid)),
            mask=mask, frame_times=(np.arange(frames)+start)*dt,
            chart_id=np.array(chart['chart_id']), labels=np.array(LABELS),
            source_game=np.array(chart['source_game']),
            difficulty=np.array(str(chart.get('difficulty') or 'unknown')),
            key_count=np.array(chart['key_count']),
            sample_rate=np.array(sample_rate), hop_length=np.array(hop))
        paths.append(path.name)
    return paths, report
