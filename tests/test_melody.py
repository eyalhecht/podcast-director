import numpy as np

from backend.melody import SR, PitchTracker


def voice(semitone_swing, seconds=40):
    """A 150 Hz tone whose pitch drifts up and down by `semitone_swing`, like speech melody."""
    t = np.arange(seconds * SR) / SR
    pitch = 150 * 2 ** (semitone_swing * np.sin(2 * np.pi * 0.3 * t) / 12)
    return 0.3 * np.sin(2 * np.pi * np.cumsum(pitch) / SR)


def melody_of(audio):
    tracker = PitchTracker()
    for i in range(0, len(audio), 4000):  # 250 ms chunks, like the browser sends
        tracker.feed(audio[i:i + 4000])
    return tracker.melody([(0.0, len(audio) / SR, 0)], 0, len(audio) / SR)


def test_a_moving_voice_scores_higher_than_a_flat_one():
    flat, lively = melody_of(voice(0.2)), melody_of(voice(6))
    assert flat < 1 < 3 < lively


def test_too_little_audio_gives_no_measure():
    assert melody_of(voice(6, seconds=3)) is None
