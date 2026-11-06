# Podcast Director - Plan

Agile: every cycle ends with something that works end to end.


## Cycle 1 - Proof of concept: "I speak, it logs" (agreed 2026-10-20)
- Laptop only (localhost), started with one command. Mic only, English.
- Plain, unstyled screen: mic dropdown (remembers last choice), Start/Stop, volume bar (Web Audio API), live words (grey interim, solid final).
- Flow: browser -> WebSocket -> FastAPI -> Deepgram (Python SDK) -> text back to screen + server log.
- Stop saves the transcript as a .txt per session.
- Done when: words appear within ~1s, volume bar moves, .txt saved on Stop.
- Extra check (not required): 2 speakers labelled correctly.
- Not in scope: remote guests / tab audio, design, AI.

## Cycle 2 - Frontend design (done 2026-10-20)
Picked "Paper" from 4 prototypes (prototypes/): off-white, serif transcript like an article, tips as margin notes. shadcn/ui + Tailwind.
Decide how the screen looks (transcript, tips panel, audio alerts, mic picker) using ui-ux-pro-max + brainstorming visual companion.

## Cycle 3 - First AI tip
One LangGraph checker (unexplained terms, OpenAI gpt-5.4-mini) runs on each final line -> term highlighted + margin note, never repeated. (built 2026-10-20)

## Later cycles
More checkers, audio-quality alerts, talk-time balance, fact-checks, post-episode report.
