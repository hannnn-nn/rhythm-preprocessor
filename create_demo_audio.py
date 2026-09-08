"""Create a short original synthetic click track; Python standard library only."""
import math
from pathlib import Path
import struct
import wave

def main():
    path = Path(__file__).parent/'examples'/'demo.wav'
    sr, duration = 22050, 5
    pulses = [.5, 1, 1.75, 2, 2.25, 2.5]
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, sr, 0, 'NONE', 'not compressed'))
        data = bytearray()
        for i in range(sr*duration):
            t = i/sr
            value = sum(.3*math.exp(-(t-p)*65)*math.sin(2*math.pi*880*(t-p))
                        for p in pulses if 0 <= t-p < .08)
            data.extend(struct.pack('<h', int(value*32767)))
        f.writeframes(data)
    print(path)

if __name__ == '__main__':
    main()
