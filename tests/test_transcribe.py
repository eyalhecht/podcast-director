"""The live WebSocket end to end, with Deepgram and the AI graphs replaced by fakes."""

import asyncio
import json
from pathlib import Path

import pytest
from deepgram.listen.v1.types import ListenV1Results
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from backend import main


def deepgram_result(text, speaker, start, is_final=True):
    words = [{"word": w, "start": start + i * 0.4, "end": start + i * 0.4 + 0.3, "confidence": 0.9, "speaker": speaker}
             for i, w in enumerate(text.split())]
    return ListenV1Results.model_validate({
        "type": "Results", "channel_index": [0, 1], "duration": 1.0, "start": start, "is_final": is_final,
        "speech_final": is_final, "channel": {"alternatives": [{"transcript": text, "confidence": 0.9, "words": words}]},
        "metadata": {"request_id": "r", "model_info": {"name": "n", "version": "v", "arch": "a"}, "model_uuid": "u"}})


class FakeDeepgram:
    """Plays back results, then ends the stream when the browser stops (or drops, if told to)."""

    def __init__(self, results, drop=False):
        self.results, self.drop, self.media = results, drop, []
        self.listen = self.v1 = self

    def connect(self, **options):
        self.options = options
        return self

    async def __aenter__(self):
        self.closed = asyncio.Event()
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_media(self, data):
        self.media.append(data)

    async def send_close_stream(self):
        self.closed.set()

    async def __aiter__(self):
        for r in self.results:
            yield r
        if self.drop:
            raise ConnectionClosedError(Close(1011, "internal error"), None)
        await self.closed.wait()


class FakeGraph:
    def __init__(self, out):
        self.out, self.calls = out, []

    async def ainvoke(self, state):
        self.calls.append(state)
        return self.out


NO_FINDINGS = {"transition": "", "followup": "", "cut_off": False, "moment": {}, "missed": {},
               "tips": [], "marks": [], "resolved": []}
NOTES = {"show_notes": {"notes": "Notes", "chapters": [{"at": "00:00", "title": "Intro"}], "titles": ["Ep"]},
         "scorecard": [{"label": "Loose ends", "value": "0 still open of 0", "verdict": "good", "why": "w"}]}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TRANSCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(main, "MEMORY", tmp_path / "memory.json")
    monkeypatch.setattr(main, "director", FakeGraph({**NO_FINDINGS, "tips": [
        {"kind": "term", "term": "LangGraph", "note": "A library for AI workflows."}]}))
    monkeypatch.setattr(main, "wrap_up", FakeGraph(NOTES))

    def start(deepgram):
        monkeypatch.setattr(main, "deepgram", deepgram)
        return TestClient(main.app)
    return start


def receive_until(ws, kind):
    messages = []
    while not messages or messages[-1]["type"] != kind:
        messages.append(ws.receive_json())
    return messages


def test_a_session_streams_the_transcript_and_saves_it(app, tmp_path):
    deepgram = FakeDeepgram([deepgram_result("We use", 0, 0.0, is_final=False),
                             deepgram_result("We use LangGraph", 0, 0.0),
                             deepgram_result("Nice", 1, 2.0)])
    with app(deepgram).websocket_connect("/ws/transcribe?lang=en", headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_bytes(b"\x00\x01" * 100)
        ws.send_text("mark")
        ws.send_text("stop")
        messages = receive_until(ws, "saved")

    assert deepgram.media == [b"\x00\x01" * 100] and deepgram.options["language"] == "en"
    transcripts = [(m["text"], m["is_final"], m["speaker"]) for m in messages if m["type"] == "transcript"]
    assert transcripts == [("We use", False, 1), ("We use LangGraph", True, 1), ("Nice", True, 2)]
    assert [m["term"] for m in messages if m["type"] == "tip"] == ["LangGraph"]  # flagged once, not per line
    assert [m["kind"] for m in messages if m["type"] == "moment"] == ["host"]
    assert [m["type"] for m in messages[-3:]] == ["notes", "scorecard", "saved"]

    saved = Path(messages[-1]["path"])
    assert saved.read_text("utf-8") == "Speaker 1: We use LangGraph\nSpeaker 2: Nice"
    assert "Marked by the host" in saved.with_suffix(".edit-list.txt").read_text("utf-8")
    assert "00:00 Intro" in saved.with_suffix(".notes.md").read_text("utf-8")
    assert json.loads((tmp_path / "memory.json").read_text("utf-8")) == [{"title": "Ep", "chapters": ["Intro"]}]


def test_unknown_languages_fall_back_to_hebrew(app):
    deepgram = FakeDeepgram([])
    with app(deepgram).websocket_connect("/ws/transcribe?lang=xx") as ws:
        ws.send_text("stop")
        receive_until(ws, "saved")
    assert deepgram.options["language"] == "he"


def test_a_dropped_deepgram_connection_still_saves_the_transcript(app):
    deepgram = FakeDeepgram([deepgram_result("Before the drop", 0, 0.0)], drop=True)
    with app(deepgram).websocket_connect("/ws/transcribe") as ws:
        messages = receive_until(ws, "saved")
    assert Path(messages[-1]["path"]).read_text("utf-8") == "Speaker 1: Before the drop"


def test_closing_the_tab_mid_episode_still_saves_the_transcript(app, tmp_path):
    deepgram = FakeDeepgram([deepgram_result("Still here", 0, 0.0)])
    with app(deepgram).websocket_connect("/ws/transcribe") as ws:
        ws.receive_json()
    saved = [p for p in tmp_path.glob("*.txt") if not p.name.endswith(".edit-list.txt")]
    assert [p.read_text("utf-8") for p in saved] == ["Speaker 1: Still here"]
