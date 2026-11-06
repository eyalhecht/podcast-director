"""Audio-only DNA measures from the raw stream: voice melody (pitch movement) and pauses.
Flat, monotone talk scores low on melody; stop-start, scripted talk has many pauses.

Same pitch method as the offline calibration, so the live numbers match it.
"""

import librosa
import numpy as np

SR, HOP, FL = 16000, 320, 1024  # 16 kHz, 20 ms hop, 64 ms frame
CHUNK = 500  # voiced frames per measurement (~10 s of voiced speech)


class PitchTracker:
    """Feed 16 kHz mono float32 audio as it arrives; keeps time, loudness and pitch per 20 ms frame."""

    def __init__(self):
        self.buf = np.zeros(0, np.float32)
        self.done = 0  # samples already turned into frames
        self.t, self.db, self.st = [], [], []

    def feed(self, pcm: np.ndarray):
        self.buf = np.concatenate([self.buf, pcm.astype(np.float32)])
        n = (len(self.buf) - FL) // HOP + 1
        if n < 50:  # work in >= 1 s blocks
            return
        seg = self.buf[: (n - 1) * HOP + FL]
        f0 = librosa.yin(seg, fmin=65, fmax=450, sr=SR, frame_length=FL, hop_length=HOP, center=False)[:n]
        rms = librosa.feature.rms(y=seg, frame_length=FL, hop_length=HOP, center=False)[0][:n]
        self.t.append((self.done + np.arange(n) * HOP + FL / 2) / SR)
        self.db.append(20 * np.log10(rms + 1e-10))
        self.st.append(12 * np.log2(f0 / 100.0))  # semitones re 100 Hz
        self.buf = self.buf[n * HOP:]
        self.done += n * HOP

    def _window(self, start, end):
        if not self.t:
            return None
        t, db, st = (np.concatenate(x) for x in (self.t, self.db, self.st))
        w = (t >= start) & (t < end)
        if w.sum() < CHUNK:
            return None
        active = db[w] > np.percentile(db[w], 95) - 25  # sound present
        return t[w], db[w], st[w], active

    def pauses(self, start: float, end: float) -> float | None:
        """Silences of 0.4 s or more per minute (gaps under 150 ms don't count, they're consonants)."""
        win = self._window(start, end)
        if win is None:
            return None
        t, _, _, active = win
        edges = np.diff(np.r_[1, active.astype(np.int8), 1])
        lengths = (np.flatnonzero(edges == 1) - np.flatnonzero(edges == -1)) * HOP / SR
        return float(np.sum(lengths >= 0.4) / ((t[-1] - t[0]) / 60))

    def melody(self, words: list[tuple[float, float, int]], start: float, end: float) -> float | None:
        """Mean over speakers of the median pitch spread (semitones) in ~10 s voiced chunks, for start..end."""
        win = self._window(start, end)
        if win is None:
            return None
        t, db, st, active = win
        plaus = (st > 12 * np.log2(0.7)) & (st < 12 * np.log2(4.3))  # 70-430 Hz
        stable = (np.abs(np.diff(st, prepend=np.inf)) < 1) | (np.abs(np.diff(st, append=np.inf)) < 1)
        voiced = active & plaus & stable & (db > np.percentile(db[active], 20))
        talk: dict[int, float] = {}
        for s, e, sp in words:
            talk[sp] = talk.get(sp, 0) + e - s
        per_speaker = []
        for sp, secs in talk.items():
            if secs < 0.1 * sum(talk.values()):
                continue
            mask = np.zeros(len(t), bool)
            for s, e, who in words:
                if who == sp:
                    mask[np.searchsorted(t, s):np.searchsorted(t, e)] = True
            v = st[mask & voiced]
            chunks = [np.std(c) for c in np.array_split(v, len(v) // CHUNK) if len(c) > 100] if len(v) >= CHUNK else []
            if chunks:
                per_speaker.append(float(np.median(chunks)))
        return float(np.mean(per_speaker)) if per_speaker else None
