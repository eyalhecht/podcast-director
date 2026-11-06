"""Live DNA nudges: measure the last 2 minutes of audio against the targets in dna.json.

Voice melody comes from the raw audio (melody.PitchTracker). Speaker switches come from Deepgram's
speaker labels only, never from its word timings: live streaming timings are choppier than the file
transcripts the targets were tuned on, so gap/pause measures don't transfer.
"""

import json
from pathlib import Path

DNA = {k: v for k, v in json.loads((Path(__file__).parent / "dna.json").read_text(encoding="utf-8")).items()
       if not k.startswith("_")}
WINDOW = 120  # seconds measured (a target can ask for a longer "window")
EVERY = 10  # seconds between checks
COOLDOWN = 180  # seconds of quiet after a nudge clears


def switches_per_min(words) -> float:
    """How often the speaker changes. Interview answers are long, so this is only judged over 5 minutes."""
    sp = [w[2] for w in words]
    return sum(a != b for a, b in zip(sp, sp[1:])) / ((words[-1][1] - words[0][0]) / 60)


def off_target(m: dict) -> list[str]:
    return [k for k, t in DNA.items() if m.get(k) is not None
            and (("max" in t and m[k] > t["max"]) or ("min" in t and m[k] < t["min"]))]


class FlowCoach:
    """Feed it final words; it returns a nudge text, None to clear, or ... for no change."""

    def __init__(self, pitch):
        self.pitch = pitch  # melody.PitchTracker fed with the same audio
        self.words, self.streak = [], {k: 0 for k in DNA}
        self.active, self.last_check, self.quiet_until = None, 0.0, 0.0

    def add(self, words):
        self.words += words
        now = self.words[-1][1] if self.words else 0
        if now < WINDOW or now - self.last_check < EVERY:
            return ...
        self.last_check = now
        recent = [w for w in self.words if w[1] >= now - WINDOW]
        if len(recent) < 20:
            return ...
        long_w = DNA["speaker_switches"]["window"]
        longer = [w for w in self.words if w[1] >= now - long_w]
        off = off_target({"voice_melody": self.pitch.melody(recent, now - WINDOW, now),
                          "speaker_switches": switches_per_min(longer) if now >= long_w else None})
        for k in DNA:
            self.streak[k] = self.streak[k] + 1 if k in off else 0
        if self.active and self.active not in off:  # back in range
            self.active, self.quiet_until = None, now + COOLDOWN
            return None
        if not self.active and now >= self.quiet_until:
            due = [k for k in DNA if self.streak[k] >= 2]
            if due:
                self.active = due[0]
                return DNA[due[0]]["nudge"]
        return ...
