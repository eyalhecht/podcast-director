"""The director graphs with a fake OpenAI client: what each node keeps, and which nodes run."""

import asyncio
from types import SimpleNamespace

import pytest

from backend import director as d


@pytest.fixture
def model(monkeypatch):
    """Answers per output schema; records which schemas were asked and with what instructions."""
    answers = {
        d.Terms: d.Terms(terms=[d.Term(term="LangGraph", note="A library."), d.Term(term="Postgres", note="A DB.")]),
        d.ComeBack: d.ComeBack(postponed=[d.Postponed(topic="Pricing", quote="later", kind="postponed")], covered=[1, 9]),
        d.Conversation: d.Conversation(transition="abrupt", followup='""', cut_off=True, missed_topic=" “Her job” ",
                                       missed_quote="I was fired", moment="clip", moment_note="Funny"),
        d.Opening: d.Opening(concrete=False, tip="Start with the story."),
        d.Memory: d.Memory(episode="Ep 41", note="Pricing models"),
        d.ShowNotes: d.ShowNotes(notes="Notes", chapters=[d.Chapter(at="00:00", title="Intro")], titles=["T"]),
    }
    asked = {}

    async def parse(*, text_format, instructions, **_):
        asked[text_format] = instructions
        return SimpleNamespace(output_parsed=answers[text_format])

    monkeypatch.setattr(d.client.responses, "parse", parse)
    return SimpleNamespace(answers=answers, asked=asked)


def run(**state):
    return asyncio.run(d.director.ainvoke({"lines": ["Speaker 1: hi"], "new": "hi", "flagged": [],
                                           "open_marks": [], **state}))


def test_every_line_gets_the_three_checkers_but_not_the_opening(model):
    out = run()
    assert set(model.asked) == {d.Terms, d.ComeBack, d.Conversation}
    assert "opening_tip" not in out


def test_already_flagged_terms_are_dropped(model):
    out = run(flagged=["langgraph"])
    assert [t["term"] for t in out["tips"]] == ["Postgres"]
    assert "langgraph" in model.asked[d.Terms]


def test_only_open_topics_can_be_resolved(model):
    out = run(open_marks=[{"id": 1, "topic": "Pricing"}])
    assert out["resolved"] == [1]
    assert out["marks"] == [{"topic": "Pricing", "quote": "later", "kind": "postponed"}]


def test_listening_is_only_judged_when_the_speaker_changed(model):
    same = run(speaker_changed=False)
    assert same["cut_off"] is False and same["missed"] == {}
    changed = run(speaker_changed=True)
    assert changed["cut_off"] is True and changed["missed"] == {"topic": "Her job", "quote": "I was fired"}
    assert changed["followup"] == ""  # the model's '""' means empty
    assert changed["moment"] == {"kind": "clip", "note": "Funny"}


def test_episode_memory_runs_only_on_a_topic_change_with_past_episodes(model):
    assert "covered_before" not in run()
    assert run(past=["Ep 41 - Pricing"])["covered_before"] == {"episode": "Ep 41", "note": "Pricing models"}
    model.asked.clear()
    model.answers[d.Conversation] = model.answers[d.Conversation].model_copy(update={"transition": ""})
    run(past=["Ep 41 - Pricing"])
    assert d.Memory not in model.asked


def test_the_opening_check_runs_when_asked(model):
    assert run(opening=True)["opening_tip"] == "Start with the story."


def test_show_notes_copy_the_feed_style_when_there_is_one(model):
    out = asyncio.run(d.wrap_up.ainvoke({"transcript": "[00:00] Speaker 1: hi", "examples": ["🎙 Our notes"],
                                         "stats": {}}))
    assert out["show_notes"]["titles"] == ["T"] and out["scorecard"]
    assert "🎙 Our notes" in model.asked[d.ShowNotes]
