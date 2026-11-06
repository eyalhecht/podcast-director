# Podcast Director

Live AI "director" for podcasters. Plan and cycles: PLAN.md. Linear: project "Podcast Director" (team Ideas).

- Use the **ponytail** skill (full) for all coding here: simplest working code, stdlib/native first, no new deps unless needed.
- UI: shadcn/ui (base-nova, Base UI) + Tailwind v4. Theme: "Paper" (prototypes/2-paper.html) - light :root vars in frontend/src/index.css, Newsreader serif transcript + Inter UI. Add components with `npx shadcn@latest add <name>` in frontend/.
- Keep replies to Eyal short.
- Run: `npm start` (dev.mjs) -> http://localhost:5173 (Vite, hot reload, proxies /ws) + FastAPI on 8000 (dev.mjs restarts it on backend .py changes; uvicorn --reload hangs on this machine).
- DNA live nudges: backend/flow.py + backend/melody.py + backend/dna.json (voice melody from raw audio, speaker switches over 5 min). Browser sends raw 16 kHz PCM. Never use Deepgram streaming word timings for gap/pause measures: they differ from file transcripts.
- Setup: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`, `npm --prefix frontend install`, `.env` with DEEPGRAM_API_KEY + OPENAI_API_KEY (never commit). AI director: backend/director.py (LangGraph + OpenAI gpt-5.4-mini; parallel nodes: unexplained_terms, come_back); live self-check (calls OpenAI): `.venv\Scripts\python backend/director.py`. Tests (no API calls): `.venv\Scripts\pip install -r requirements-dev.txt`, then `.venv\Scripts\python -m pytest`.
