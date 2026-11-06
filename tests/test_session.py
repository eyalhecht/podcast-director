import pytest

from backend import session as session_module
from backend.session import Session, clock


@pytest.fixture
def now(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(session_module.time, "monotonic", lambda: t[0])
    return t


def result(**found):
    return {"transition": "", "followup": "", "cut_off": False, "moment": {}, "missed": {},
            "tips": [], "marks": [], "resolved": [], **found}


def kinds(messages):
    return [m.get("kind", m["type"]) for m in messages]


def test_clock():
    assert clock(0) == "00:00"
    assert clock(754.9) == "12:34"


def test_same_speaker_phrases_join_one_paragraph():
    s = Session([])
    assert s.add_final(1, "Hi.", 0) is False  # the first line is nobody's reply
    assert s.add_final(1, "Welcome.", 2) is False
    assert s.add_final(2, "Thanks.", 65) is True
    assert s.lines == ["Speaker 1: Hi. Welcome.", "Speaker 2: Thanks."]
    assert s.timed[-1] == "[01:05] Speaker 2: Thanks."


def test_opening_check_runs_once_after_five_minutes_on_the_whole_transcript():
    s = Session(["Ep 1 - pricing"])
    for i in range(8):
        s.add_final(i % 2, f"line {i}", i)
    assert s.director_input("x", 120, False)["opening"] is False
    first = s.director_input("x", 301, True)
    assert first["opening"] is True and len(first["lines"]) == 8 and first["past"] == ["Ep 1 - pricing"]
    later = s.director_input("x", 400, False)
    assert later["opening"] is False and len(later["lines"]) == 5


def test_terms_are_flagged_once_including_other_forms(now):
    s = Session([])
    assert kinds(s.review(result(tips=[{"kind": "term", "term": "Kubernetes", "note": "n"}]), 0)) == ["term"]
    now[0] += 60
    assert s.review(result(tips=[{"kind": "term", "term": "kubernetes clusters", "note": "n"}]), 0) == []
    assert s.flagged == ["Kubernetes"]


def test_terms_are_at_most_one_per_15_seconds(now):
    s = Session([])
    tips = [{"kind": "term", "term": "LangGraph", "note": "n"}, {"kind": "term", "term": "Postgres", "note": "n"}]
    assert [m["term"] for m in s.review(result(tips=tips), 0)] == ["LangGraph"]
    now[0] += 15
    assert [m["term"] for m in s.review(result(tips=tips), 0)] == ["Postgres"]


def test_postponed_topics_open_once_and_close_when_covered(now):
    s = Session([])
    sent = s.review(result(marks=[{"topic": "Pricing", "quote": "later", "kind": "postponed"}]), 0)
    assert sent == [{"type": "mark", "id": 1, "topic": "Pricing", "quote": "later", "kind": "postponed"}]
    assert s.review(result(marks=[{"topic": "pricing", "quote": "again", "kind": "postponed"}]), 0) == []
    assert s.review(result(resolved=[1, 7]), 0) == [{"type": "resolved", "id": 1}]
    assert s.open_marks == [] and s.marks_total == 1


def test_hanging_stories_are_at_most_one_per_2_minutes(now):
    s = Session([])

    def hanging(topic):
        return result(marks=[{"topic": topic, "quote": "q", "kind": "hanging"}])

    assert len(s.review(hanging("The hospital"), 0)) == 1
    now[0] += 60
    assert s.review(hanging("The trip"), 0) == []
    now[0] += 60
    assert len(s.review(hanging("The trip"), 0)) == 1


def test_a_skipped_point_becomes_a_come_back_mark(now):
    s = Session([])
    sent = s.review(result(missed={"topic": "Her first job", "quote": "q"}), 0)
    assert sent[0]["kind"] == "their_point" and s.open_marks[0]["topic"] == "Her first job"


def test_transitions_nudge_only_when_announcing_becomes_a_habit(now):
    s = Session([])
    assert s.review(result(transition="announced"), 0) == []
    assert s.review(result(transition="bridge"), 0) == []
    assert kinds(s.review(result(transition="announced"), 0)) == ["transition"]
    assert s.transitions == ["announced", "bridge", "announced"]


def test_craft_tips_have_a_cooldown_and_are_counted(now):
    s = Session([])
    assert kinds(s.review(result(followup="Why did you almost quit?", cut_off=True), 0)) == ["followup", "let_finish"]
    now[0] += 100
    assert s.review(result(followup="And then?", cut_off=True), 0) == []
    now[0] += 30
    assert kinds(s.review(result(followup="And then?"), 0)) == ["followup"]
    assert s.tip_counts == {"followup": 2, "let_finish": 1}


def test_a_past_episode_is_mentioned_once(now):
    s = Session([])
    covered = {"episode": "Ep 41", "note": "Per-seat pricing"}
    assert s.review(result(covered_before=covered), 0)[0]["note"] == "Ep 41: Per-seat pricing"
    now[0] += 1000
    assert s.review(result(covered_before=covered), 0) == []


def test_sponsor_reads_and_clips_get_one_edit_list_mark_a_minute(now):
    s = Session([])
    sponsor = result(moment={"kind": "sponsor", "note": "ad ended"})
    assert s.review(sponsor, 75) == [{"type": "moment", "at": "01:15", "kind": "sponsor", "note": "ad ended"}]
    now[0] += 30
    assert s.review(sponsor, 105) == []
    assert len(s.review(result(moment={"kind": "retake", "note": "flub"}), 106)) == 1
    assert len(s.moments) == 2


def test_stats_share_talk_time_and_drop_phantom_speakers():
    s = Session([])
    words = [(0, 60, 0), (60, 99, 1), (99, 100, 2)]
    stats = s.stats(words, 4.2)
    assert stats["talk"] == {0: 0.6, 1: 0.39}
    assert stats["melody"] == 4.2 and stats["switches"] == pytest.approx(1.2)
    assert s.stats([], None)["switches"] is None


def test_a_past_episode_found_during_the_cooldown_can_still_show_later(now):
    s = Session([])
    s.review(result(covered_before={"episode": "Ep 41", "note": "n"}), 0)
    now[0] += 60
    assert s.review(result(covered_before={"episode": "Ep 7", "note": "n"}), 0) == []
    now[0] += 120
    assert s.review(result(covered_before={"episode": "Ep 7", "note": "n"}), 0)[0]["note"] == "Ep 7: n"
