# Podcast Director

[![CI](https://github.com/eyalhecht/podcast-director/actions/workflows/ci.yml/badge.svg)](https://github.com/eyalhecht/podcast-director/actions/workflows/ci.yml)

A producer in the room for podcasters who record without one. It listens while you record and, on one screen next to the live transcript, tells you what a producer would whisper: "explain that term", "you said you'd come back to pricing", "they were mid-story, let them finish", "voices sound flat". When you press Stop it writes the show notes, chapters and title ideas, and scores how the conversation went.

**Status: in progress, a working prototype.** It runs on one laptop for one host, it is built for Hebrew shows first, and it has only been tested on Windows. See [Limits](#limits) before you try it.

## Why it is interesting

Most podcast AI tools work after the recording: upload a file, get a transcript and notes. This one works *during* the conversation, when advice can still change the episode. That brings a few problems that are fun to solve:

- **Latency.** Words have to appear within about a second, while several model calls run on every sentence. The AI runs beside the transcript (`asyncio` tasks), so a slow call never delays the words.
- **Not nagging.** A director who talks all the time gets ignored. Every kind of tip has its own cooldown, terms are only explained once (plurals and prefixed forms too), and loose ends are crossed off when the hosts get back to them. Those rules live in [`backend/session.py`](backend/session.py) and are unit tested.
- **Measuring "a good conversation".** The voice-melody and back-and-forth nudges are not guesses. They come from an offline study that measured about 29 hours of public Hebrew episodes, good shows against flat ones. The targets live in [`backend/dna.json`](backend/dna.json). Pitch is computed from the raw audio, not from transcript timings, because live streaming timings did not match the file transcripts the targets were tuned on.

## What it does

While recording:

- Live transcript with speaker labels, and talk-time share per speaker
- Audio warnings in the browser: clipping, or no sound for 8 seconds
- Margin notes: terms listeners may not know, with a one-line explanation the host can say out loud
- "Come back to": topics someone postponed, stories left hanging, and a point the other speaker made that got skipped; crossed off when covered
- Craft tips: a deeper follow-up question, "let them finish", too many announced topic changes, an opening that only lists an agenda (checked once, around minute 5)
- "Covered before": the new topic was already covered in a past episode, from your RSS feed and from sessions recorded in this app
- Flow nudges from the audio: flat voices, long monologues
- Edit list for the editor: clips, retakes, sponsor reads, and your own marks (press M)
- Optional rundown with a timer per segment

After you press Stop:

- Show notes, chapters and three title ideas in Hebrew, copying the style of your show's previous notes when you give an RSS feed
- A "Was it a good talk?" scorecard compared with the measured good shows (no AI)
- Files in `transcripts/`: the transcript `.txt`, `.edit-list.txt`, `.notes.md`, and `memory.json` for "covered before"

## How it works

```
browser mic ── 16 kHz PCM over WebSocket ──> FastAPI (backend/main.py)
                                               ├─> Deepgram nova-3 streaming (diarized) ──> transcript back to the browser
                                               ├─> pitch tracker (librosa) + flow coach   ──> flow nudges
                                               └─> per final phrase: LangGraph director (OpenAI) ──> Session rules ──> tips, marks
Stop ──> wrap-up graph: show notes (OpenAI) + scorecard (no AI) ──> transcripts/
```

- [`backend/director.py`](backend/director.py): the LangGraph graphs. Live: `unexplained_terms`, `come_back` and `conversation` run in parallel on every final phrase; `memory` runs only after a topic change; `opening` once. After the episode: `show_notes` and `scorecard` in parallel. Model: `gpt-5.4-mini` with structured outputs.
- [`backend/session.py`](backend/session.py): the session state and the rules for what reaches the screen.
- [`backend/flow.py`](backend/flow.py), [`backend/melody.py`](backend/melody.py): the audio-based nudges.
- [`frontend/src/App.tsx`](frontend/src/App.tsx): React 19 + Vite + Tailwind v4 + shadcn/ui, one screen.

## Run it

You need Python 3.12, Node 22, a [Deepgram](https://deepgram.com) API key and an [OpenAI](https://platform.openai.com) API key. Both services charge per use: every final phrase sends three model calls to OpenAI, and Deepgram bills per minute of audio.

```sh
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
npm --prefix frontend install
```

Create `.env` in the repo root (it is git-ignored):

```
DEEPGRAM_API_KEY=...
OPENAI_API_KEY=...
```

Then:

```sh
npm start
```

This starts FastAPI on port 8000 and Vite on http://localhost:5173 (it restarts the backend when a `.py` file changes). Open the page, allow the microphone, press Start and talk. The RSS field is optional.

## Tests

```sh
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The tests never call Deepgram or OpenAI: they use a fake Deepgram connection, a fake OpenAI client and synthetic audio. They cover the WebSocket session end to end (including a dropped Deepgram connection), the show/skip rules, the graph routing, the flow nudges, the pitch tracker, RSS parsing and the scorecard. CI runs them, plus the frontend lint and build, on every pull request.

Two checks do use the real APIs and cost money, so they are manual: `python backend/director.py` (one case per checker against the real model) and `python backend/smoke_check.py some.wav` (streams a 16 kHz 16-bit mono WAV to a running server).

## Limits

- **Hebrew first.** A live microphone is always transcribed as Hebrew, and the show notes are written in Hebrew. English works only through the demo options, which play local clips that are not in the repo.
- **One laptop, one host.** No accounts or auth. The server listens on localhost and refuses WebSocket connections from other websites. Remote guests are not captured; it hears what your microphone hears.
- **Speaker labels come from Deepgram's diarization** and are sometimes wrong, which affects talk time and the listening tips.
- **The AI is noisy.** The tips are tuned with cooldowns and prompts, not measured for precision. The flow nudges are the calibrated part.
- **Fixed audio thresholds.** Clipping and silence warnings use fixed levels and may misfire on some microphones.
- **Tested on Windows only.** `dev.mjs` handles macOS/Linux paths, but nobody has run it there yet.

[`PLAN.md`](PLAN.md) has the build history by cycle. The offline study behind the nudges is not in this repo, because it was built on other people's episodes; only the resulting targets in `backend/dna.json` are.

## Licence

All rights reserved; the code is public to read, not to reuse ([LICENSE](LICENSE)).
