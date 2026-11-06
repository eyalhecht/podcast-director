from backend.flow import DNA, FlowCoach, off_target, switches_per_min


class FakePitch:
    def __init__(self, melody):
        self.value = melody

    def melody(self, words, start, end):
        return self.value


def talk(start, end, turn=2.0):
    """Half-second words, the speaker changing every `turn` seconds."""
    words, t = [], start
    while t < end:
        words.append((t, t + 0.5, int(t // turn) % 2))
        t += 0.5
    return words


def test_switches_per_min_counts_speaker_changes():
    assert switches_per_min([(0, 1, 0), (1, 2, 1), (2, 3, 1), (59, 60, 0)]) == 2


def test_off_target_ignores_missing_measures():
    assert off_target({"voice_melody": 1.0, "speaker_switches": None}) == ["voice_melody"]
    assert off_target({"voice_melody": 9.0, "speaker_switches": 5.0}) == []


def test_waits_for_two_minutes_of_talk():
    coach = FlowCoach(FakePitch(1.0))
    assert coach.add(talk(0, 100)) is ...


def test_flat_voice_nudges_after_two_checks_then_clears_and_cools_down():
    pitch = FakePitch(1.0)
    coach = FlowCoach(pitch)
    assert coach.add(talk(0, 121)) is ...  # first off-target check
    assert coach.add(talk(121, 132)) == DNA["voice_melody"]["nudge"]
    assert coach.active == "voice_melody"

    pitch.value = 5.0
    assert coach.add(talk(132, 143)) is None  # back in range: clear the nudge

    pitch.value = 1.0
    assert coach.add(talk(143, 154)) is ...
    assert coach.add(talk(154, 165)) is ...  # still in the quiet period


def test_long_monologues_nudge_only_after_five_minutes():
    coach = FlowCoach(FakePitch(5.0))
    assert coach.add(talk(0, 290, turn=1000)) is ...
    assert coach.add(talk(290, 301, turn=1000)) is ...
    assert coach.add(talk(301, 312, turn=1000)) == DNA["speaker_switches"]["nudge"]
