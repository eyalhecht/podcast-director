"""Podcast Director POC: browser audio -> Deepgram -> live transcript back to the browser."""

import asyncio
import html
import json
import re
import urllib.request
import logging
import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from deepgram import AsyncDeepgramClient
from deepgram.listen.v1.types import ListenV1Results
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from backend.director import director, wrap_up
from backend.flow import FlowCoach
from backend.melody import PitchTracker
from backend.session import Session

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TRANSCRIPTS_DIR = ROOT / "transcripts"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("podcast-director")

app = FastAPI()
deepgram = AsyncDeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])


def to_message(result: ListenV1Results) -> dict | None:
    """Turn a Deepgram result into {text, is_final, speaker}, or None if empty."""
    alt = result.channel.alternatives[0]
    if not alt.transcript:
        return None
    speakers = [w.speaker for w in alt.words if w.speaker is not None]
    return {
        "type": "transcript",
        "text": alt.transcript,
        "is_final": bool(result.is_final),
        "speaker": max(set(speakers), key=speakers.count) + 1 if speakers else None,  # people count from 1
    }


def feed_items(url: str) -> list[tuple[str, str]]:
    """(title, show notes as plain text) of the show's latest episodes, from its RSS feed."""
    if urlparse(url).scheme not in ("http", "https"):  # urlopen would also read local files
        raise ValueError(f"Not a web feed: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return parse_feed(urllib.request.urlopen(request, timeout=15).read().decode("utf-8", "ignore"))


def parse_feed(xml: str, n: int = 60) -> list[tuple[str, str]]:
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:n]:
        texts = []
        for tag in ("title", "content:encoded|description"):
            m = re.search(rf"<({tag})>(.*?)</\1>", item, re.S)
            text = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(2))) if m else ""
            text = re.sub(r"<br\s*/?>|</p>", "\n", text)
            texts.append(re.sub(r"<[^>]+>", "", text).strip()[:3000])
        out.append((texts[0], texts[1]))
    return out


MEMORY = TRANSCRIPTS_DIR / "memory.json"  # past sessions: [{"title", "chapters"}]


def past_episodes(items: list[tuple[str, str]]) -> list[str]:
    """One line per past episode, from the feed and from sessions recorded in this app."""
    past = [f"{t} - {d[:200]}" for t, d in items]
    if MEMORY.exists():
        past += [f"{e['title']} - {', '.join(e['chapters'])}" for e in json.loads(MEMORY.read_text("utf-8"))]
    return past[-80:]


def save_transcript(lines: list[str], moments: list[dict]) -> Path:
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPTS_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    if moments:  # the editor's checklist: retakes to cut, clips to pull, sponsor reads done
        path.with_suffix(".edit-list.txt").write_text(
            "\n".join(f"{m['at']}  {m['kind']:8s} {m['note']}" for m in moments), encoding="utf-8")
    return path


@app.websocket("/ws/transcribe")
async def transcribe(ws: WebSocket, feed: str = "", lang: str = "he"):
    # Any website can open a WebSocket to localhost, and this one spends the host's API credit.
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
        log.warning("Refused connection from %s", origin)
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        items = await asyncio.to_thread(feed_items, feed) if feed else []
    except Exception:
        log.exception("Feed failed: %s", feed)
        items = []
    session = Session(past_episodes(items))
    pending: set[asyncio.Task] = set()
    pitch = PitchTracker()
    coach = FlowCoach(pitch)
    last_end = 0.0  # audio time of the latest word, for the host's manual marks
    log.info("Session started")

    async def direct(new: str, spoken: float, changed: bool, at: float):
        # Runs beside the transcript so a slow AI call never delays the words.
        try:
            result = await director.ainvoke(session.director_input(new, spoken, changed))
            for message in session.review(result, at):
                log.info("Director: %s", message)
                await ws.send_json(message)
        except Exception:
            log.exception("Director failed")

    # Browser sends raw 16 kHz 16-bit mono PCM, so the same bytes feed Deepgram and the pitch tracker.
    async with deepgram.listen.v1.connect(
        model="nova-3",
        encoding="linear16",
        sample_rate="16000",
        language=lang if lang in ("he", "en") else "he",  # Hebrew by default; English for testing
        interim_results="true",
        diarize="true",
        punctuate="true",  # smart_format turns Hebrew number words into wrong digits
    ) as dg:

        async def browser_to_deepgram():
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await dg.send_media(msg["bytes"])
                    pcm = msg["bytes"][: len(msg["bytes"]) // 2 * 2]  # whole 16-bit samples only
                    pitch.feed(np.frombuffer(pcm, np.int16) / 32768)
                elif msg.get("text") == "mark":  # host pressed M
                    await ws.send_json(session.moment(last_end, "host", "Marked by the host"))
                elif msg.get("text") == "stop":
                    break
            with suppress(ConnectionClosed):  # Deepgram may have closed already
                await dg.send_close_stream()

        async def deepgram_to_browser():
            nonlocal last_end
            async for result in dg:
                if not isinstance(result, ListenV1Results):
                    continue
                message = to_message(result)
                if message is None:
                    continue
                if message["is_final"]:
                    changed = session.add_final(message["speaker"], message["text"], result.start)
                    log.info("Speaker %s: %s", message["speaker"], message["text"])
                    words = result.channel.alternatives[0].words
                    nudge = coach.add([(w.start, w.end, w.speaker or 0) for w in words])
                    if nudge is not ...:
                        if nudge:
                            kind = {"voice_melody": "flat", "speaker_switches": "monologue"}[coach.active]
                            session.tip_counts[kind] = session.tip_counts.get(kind, 0) + 1
                        log.info("Nudge: %s", nudge)
                        await ws.send_json({"type": "nudge", "text": nudge})
                    last_end = words[-1].end if words else last_end
                    task = asyncio.create_task(direct(message["text"], last_end, changed,
                                                      words[0].start if words else last_end))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                await ws.send_json(message)

        reader = asyncio.create_task(browser_to_deepgram())
        try:
            await deepgram_to_browser()  # Deepgram ends the stream after "stop", or drops it
        except (ConnectionClosed, WebSocketDisconnect):
            log.warning("Connection dropped, saving what we have", exc_info=True)
        reader.cancel()
        with suppress(asyncio.CancelledError, ConnectionClosed, WebSocketDisconnect):
            await reader
        await asyncio.gather(*pending)  # let the last tips arrive before closing

    path = save_transcript(session.lines, session.moments)
    if session.lines:  # after-episode graph: show notes in the show's own format
        try:
            examples = [notes_text for _, notes_text in items[:2]]
            words = coach.words  # (start, end, speaker) of the whole session
            stats = session.stats(words, pitch.melody(words, 0, last_end) if words else None)
            out = await wrap_up.ainvoke({"transcript": "\n".join(session.timed), "examples": examples, "stats": stats})
            notes, card = out["show_notes"], out["scorecard"]
            chapters = "\n".join(f"{c['at']} {c['title']}" for c in notes["chapters"])
            score = "\n".join(f"{r['label']}: {r['value']} ({r['verdict']})" for r in card)
            path.with_suffix(".notes.md").write_text(
                f"{notes['notes']}\n\n{chapters}\n\nTitle ideas:\n" + "\n".join(notes["titles"])
                + f"\n\nScorecard:\n{score}", encoding="utf-8")
            await ws.send_json({"type": "notes", "styled_on_feed": bool(examples), **notes})
            await ws.send_json({"type": "scorecard", "rows": card})
            mem = json.loads(MEMORY.read_text("utf-8")) if MEMORY.exists() else []
            mem.append({"title": notes["titles"][0] if notes["titles"] else path.stem, "chapters": [c["title"] for c in notes["chapters"]]})
            MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            log.exception("Show notes failed")
    log.info("Session ended, saved %s", path)
    try:
        await ws.send_json({"type": "saved", "path": str(path)})
        await ws.close()
    except Exception:
        pass  # browser already gone
