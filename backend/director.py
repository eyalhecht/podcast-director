"""The AI director: a LangGraph graph that reads the newest transcript text and returns tips.

Checkers are nodes that run side by side on the same input:
- unexplained_terms: words listeners may not know, with a ready-to-say explanation
- come_back: topics the hosts postponed ("we'll get back to that") or stories/questions left hanging, and when covered
- conversation: announced topic jumps and shallow follow-ups (the content DNA from the offline study)
- opening: runs once around minute 5 - did the episode open with a concrete story/problem or only an agenda
"""

from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
MODEL = "gpt-5.4-mini"
client = AsyncOpenAI()  # reads OPENAI_API_KEY


class State(TypedDict, total=False):
    lines: list[str]  # recent transcript context, newest last
    new: str  # the text that just arrived; checkers only judge this
    flagged: list[str]  # terms already shown this session
    open_marks: list[dict]  # postponed topics still open: {id, topic}
    tips: list[dict]  # out: new term tips
    marks: list[dict]  # out: new postponed topics {topic, quote}
    resolved: list[int]  # out: ids of open marks that just got covered
    opening: bool  # in: also run the one-time opening check on lines
    speaker_changed: bool  # in: the NEW TEXT is from a different speaker than the text before it
    cut_off: bool  # out: the new speaker cut the other one off mid-thought and steered away
    missed: dict  # out: {"topic", "quote"} of an interesting point the new speaker skipped, or {}
    moment: dict  # out: {"kind": clip/retake/sponsor, "note"} for the edit list, or {}
    transition: str  # out: "", "announced", "bridge", "natural" or "abrupt" - how the new text changed topic
    followup: str  # out: a deeper question the host could have asked, "" if none needed
    opening_tip: str  # out: from the opening check, "" if the opening was fine
    past: list[str]  # in: past episodes ("title: what it covered"), from the feed and past sessions
    covered_before: dict  # out: {"episode", "note"} when a new topic repeats a past episode, or {}


async def ask(instructions: str, state: State, schema: type[BaseModel]):
    resp = await client.responses.parse(
        model=MODEL,
        reasoning={"effort": "low"},
        instructions=instructions,
        input="CONTEXT:\n" + "\n".join(state["lines"]) + "\n\nNEW TEXT:\n" + state["new"],
        text_format=schema,
    )
    return resp.output_parsed


class Term(BaseModel):
    term: str  # exactly as spoken, so the UI can highlight it
    note: str  # the explanation itself, ready for the host to say


class Terms(BaseModel):
    terms: list[Term]


TERMS_PROMPT = """You are a podcast producer whispering to the host.
Find terms in the NEW TEXT that a general Israeli podcast listener probably doesn't know and that were not explained:
technical jargon, acronyms, niche products, obscure people or events. Usually there are none; at most one or two.
Skip:
- everyday words and loanwords most Israelis use (פרילנסר, אפליקציה, סטארטאפ, פיצ'ר, דאטה, פידבק, AI, ChatGPT)
- anything already explained, and any other form (plural, prefix, spelling) of a term already flagged
- anything that looks garbled or misheard by the transcription, or that you don't confidently recognize: never guess
Already flagged: {flagged}
For each: the term exactly as written, and the explanation itself, always in plain English, under 15 words,
ready for the host to say out loud. Write the explanation, not advice.
Good: "Software that runs and manages many app containers across servers."
Bad: "Briefly explain it as container orchestration." """


async def unexplained_terms(state: State) -> dict:
    found = await ask(TERMS_PROMPT.format(flagged=", ".join(state["flagged"]) or "none"), state, Terms)
    seen = {f.lower() for f in state["flagged"]}
    return {"tips": [{"kind": "term", "term": t.term, "note": t.note}
                     for t in found.terms if t.term.lower() not in seen]}


class Postponed(BaseModel):
    topic: str  # 1-4 words, e.g. "Pricing"
    quote: str  # the sentence where it was postponed or cut off, as written
    kind: str  # "postponed" or "hanging"


class ComeBack(BaseModel):
    postponed: list[Postponed]
    covered: list[int]  # ids of open topics


COME_BACK_PROMPT = """You are a podcast producer keeping track of loose ends.
1. postponed: things left open in the NEW TEXT, each with kind:
   - "postponed": a speaker explicitly puts a topic off ("we'll get back to that", "more on that later", "remind me to talk about X")
   - "hanging": a story with a clear build-up in the CONTEXT ("so I'm in the hospital and the doctor says...") is cut
     off before its ending because the NEW TEXT, from a DIFFERENT speaker, jumps to another subject; or a real question
     in the CONTEXT is skipped and nobody answers it. The transcript arrives in fragments, so a sentence that just stops
     mid-way is NOT hanging - the same speaker usually continues it. Rare: at most a few per episode.
   topic: 1-4 words naming what is open, e.g. "Pricing", "The hospital story".
2. covered: ids of OPEN TOPICS that the NEW TEXT actually discusses now, in substance.
   Mentioning a topic or postponing it again does not count.
OPEN TOPICS: {open}
Usually both lists are empty."""


async def come_back(state: State) -> dict:
    open_ = "; ".join(f"{m['id']}: {m['topic']}" for m in state["open_marks"]) or "none"
    found = await ask(COME_BACK_PROMPT.format(open=open_), state, ComeBack)
    ids = {m["id"] for m in state["open_marks"]}
    return {"marks": [p.model_dump() for p in found.postponed],
            "resolved": [i for i in found.covered if i in ids]}


class Conversation(BaseModel):
    transition: str  # "" if the NEW TEXT stays on topic, else "announced" / "bridge" / "natural" / "abrupt"
    followup: str  # English, under 15 words: the deeper question to ask, or ""
    cut_off: bool
    missed_topic: str  # 1-4 words, or ""
    missed_quote: str  # the other speaker's words that were skipped, as written, or ""
    moment: str  # "clip", "retake", "sponsor" or ""
    moment_note: str  # English, under 10 words, why it's marked


CONVERSATION_PROMPT = """You are a podcast producer coaching the host on conversation craft. Judge only the NEW TEXT.
transition: if the NEW TEXT moves to a new topic, how? bridge = links old and new topic; natural = grows out of what
was just said; announced = the host just announces the next item ("עכשיו נעבור ל...", "שאלה הבאה"); abrupt = no link.
A question about a different subject counts as a topic change. "" only if the NEW TEXT stays on the same subject.
followup: only if, right before the NEW TEXT, the other speaker said something surprising, emotional or a strong claim
(a personal confession, a near-failure, a bold opinion) AND the NEW TEXT moves on without digging in. Then write, in English, the deeper question (an example, what happened next, why, the
contradiction). Otherwise "". Usually "".
SPEAKER CHANGED: {changed}. Only if yes, also judge listening (otherwise cut_off=false, missed_topic=""):
cut_off: true only if the previous speaker was clearly in the middle of a thought or story (a build-up with no point yet)
and the NEW TEXT talks over it and takes the conversation elsewhere. Overlap, agreement and quick reactions are normal
and NOT cut_off. Rare.
missed_topic / missed_quote: only if the previous speaker raised a specific, interesting point (a detail, a feeling, an
opinion) and the NEW TEXT ignores it and moves to its own point. Name it in 1-4 words and quote the skipped words. Usually "".
moment: mark the NEW TEXT for the editor, else "":
clip = a strong standalone line worth a social clip (funny, emotional, bold, surprising). Rare: a few per episode.
retake = a speaker flubs and restarts or asks to cut ("רגע, אני אגיד את זה שוב", "תחתכו את זה", "let me redo that").
sponsor = a sponsor or ad read just ended. moment_note: why, in English, under 10 words. """


def clean(text: str) -> str:
    return text.strip().strip("\"'“”").strip()  # the model sometimes writes "" instead of an empty string


async def conversation(state: State) -> dict:
    changed = state.get("speaker_changed", False)
    found = await ask(CONVERSATION_PROMPT.format(changed="yes" if changed else "no"), state, Conversation)
    topic = clean(found.missed_topic)
    return {"transition": clean(found.transition), "followup": clean(found.followup), "cut_off": changed and found.cut_off,
            "missed": {"topic": topic, "quote": clean(found.missed_quote)} if changed and topic else {},
            "moment": {"kind": clean(found.moment), "note": clean(found.moment_note)} if clean(found.moment) else {}}


class Opening(BaseModel):
    concrete: bool  # opened with a concrete story, scene, problem or mystery
    tip: str  # English, under 15 words, "" if concrete


OPENING_PROMPT = """You are a podcast producer. These are the first minutes of an episode. Good shows get to a concrete
story, scene, problem or mystery fast; flat ones only announce an agenda ("today we'll talk about X, Y and Z") and
categories. Did this opening reach something concrete? If not, give one tip for the host in English, under 15 words."""


async def opening(state: State) -> dict:
    resp = await client.responses.parse(model=MODEL, reasoning={"effort": "low"}, instructions=OPENING_PROMPT,
                                        input="\n".join(state["lines"]), text_format=Opening)
    return {"opening_tip": "" if resp.output_parsed.concrete else clean(resp.output_parsed.tip)}


class Memory(BaseModel):
    episode: str  # the past episode's title, or ""
    note: str  # English, under 15 words: what was already said there


MEMORY_PROMPT = """You are a podcast producer with the show's episode list. The hosts just moved to the topic in the
NEW TEXT. If a PAST EPISODE already covered this same specific topic in depth, give its title as written and say what was
covered, always in English, under 15 words, so the host can reference it or add something new. Same broad area is not enough. Usually "".
PAST EPISODES:
{past}"""


async def memory(state: State) -> dict:
    found = await ask(MEMORY_PROMPT.format(past="\n".join(state["past"])), state, Memory)
    return {"covered_before": {"episode": clean(found.episode), "note": clean(found.note)} if clean(found.episode) else {}}


PER_TEXT = ["unexplained_terms", "come_back", "conversation"]  # run in parallel on every new text

graph = StateGraph(State)
for fn in (unexplained_terms, come_back, opening, memory):
    graph.add_node(fn)
    graph.add_edge(fn.__name__, END)
graph.add_node(conversation)
# Memory only runs after conversation saw a topic change, so the long episode list isn't sent on every sentence.
graph.add_conditional_edges("conversation", lambda s: "memory" if s.get("transition") and s.get("past") else END,
                            ["memory", END])
graph.add_conditional_edges(START, lambda s: PER_TEXT + (["opening"] if s.get("opening") else []),
                            PER_TEXT + ["opening"])
director = graph.compile()


# --- After the episode: a small graph that runs once when the host presses Stop ---

class Chapter(BaseModel):
    at: str  # mm:ss
    title: str


class ShowNotes(BaseModel):
    notes: str  # Hebrew, ready to paste
    chapters: list[Chapter]
    titles: list[str]  # 3 Hebrew title ideas


class WrapUp(TypedDict, total=False):
    transcript: str  # "[mm:ss] Speaker N: text" lines
    examples: list[str]  # the show's own recent show notes, if a feed was given
    show_notes: dict
    stats: dict  # what the live checkers logged (see main.py)
    scorecard: list[dict]  # {label, value, verdict: good/ok/work, why}


NOTES_PROMPT = """Write show notes in Hebrew for this podcast episode (auto-transcribed, expect some misheard words).
{style}
Also give chapters (mm:ss from the transcript timestamps, 4-10 of them, short Hebrew titles) and 3 Hebrew episode titles."""


async def show_notes(state: WrapUp) -> dict:
    ex = state.get("examples") or []
    style = ("Copy the structure, length, tone and formatting of the show's own previous show notes below "
             "(sections, emoji, links lines, credits), but with this episode's content:\n\n" + "\n\n---\n\n".join(ex)
             if ex else "Use a short paragraph about the episode, then bullet points of what's covered.")
    resp = await client.responses.parse(model=MODEL, reasoning={"effort": "low"}, instructions=NOTES_PROMPT.format(style=style),
                                        input=state["transcript"], text_format=ShowNotes)
    return {"show_notes": resp.output_parsed.model_dump()}


def scorecard(state: WrapUp) -> dict:
    """No AI: compares what the live checkers logged with the good shows from the offline study."""
    s = state["stats"]
    rows = []

    def row(label, value, verdict, why):
        rows.append({"label": label, "value": value, "verdict": verdict, "why": why})

    if s.get("melody") is not None:
        m = s["melody"]
        row("Voice melody", f"{m:.1f}", "good" if m >= 4.3 else "ok" if m >= 3.8 else "work",
            "Good shows move about 4.8 semitones, flat ones about 3.2.")
    if s.get("switches") is not None:
        w = s["switches"]
        row("Back-and-forth", f"{w:.1f} switches/min", "good" if w >= 3 else "ok" if w >= 1 else "work",
            "Good shows switch speakers 3+ times a minute.")
    if s.get("transitions"):
        a = s["transitions"].count("announced") / len(s["transitions"])
        row("Topic changes", f"{a:.0%} just announced", "good" if a <= 0.4 else "ok" if a <= 0.55 else "work",
            "Good shows announce about 1 in 3 topic changes, flat ones 2 in 3.")
    if s.get("talk"):
        top = max(s["talk"].values())
        row("Talk balance", " / ".join(f"{v:.0%}" for v in s["talk"].values()), "good" if top <= 0.7 else "ok",
            "Interviews can be uneven; conversations usually stay under 70/30.")
    left = s.get("open_marks", 0)
    row("Loose ends", f"{left} still open of {s.get('marks', 0)}", "good" if left == 0 else "ok",
        "Topics you postponed or left hanging and never came back to.")
    for kind, label in (("followup", "Missed follow-ups"), ("let_finish", "Cut-offs"), ("flat", "Flat-voice nudges")):
        n = s.get("tips", {}).get(kind, 0)
        row(label, str(n), "good" if n == 0 else "ok" if n <= 2 else "work", "How often the director had to step in.")
    return {"scorecard": rows}


wrap = StateGraph(WrapUp)
for fn in (show_notes, scorecard):  # both run in parallel
    wrap.add_node(fn)
    wrap.add_edge(START, fn.__name__)
    wrap.add_edge(fn.__name__, END)
wrap_up = wrap.compile()


if __name__ == "__main__":
    # Self-check, needs OPENAI_API_KEY: one case per checker.
    import asyncio

    def run(lines, **kw):
        return director.ainvoke({"lines": lines, "new": lines[-1].split(": ", 1)[1],
                                 "flagged": [], "open_marks": [], **kw})

    async def demo():
        lines = ["Speaker 0: Welcome back to the show.",
                 "Speaker 1: Honestly it's mostly LangGraph and a lot of prompts. We'll get back to pricing later."]
        first = await run(lines)
        assert any("langgraph" in t["term"].lower() for t in first["tips"]), first["tips"]
        assert any("pric" in m["topic"].lower() for m in first["marks"]), first["marks"]
        again = await run(lines, flagged=["LangGraph"])
        assert not any("langgraph" in t["term"].lower() for t in again["tips"])
        later = await run(lines + ["Speaker 0: OK, pricing. We charge 20 dollars a month per seat, and teams get a discount."],
                          open_marks=[{"id": 1, "topic": "Pricing"}])
        assert later["resolved"] == [1], later["resolved"]
        story = await run(["Speaker 1: So I'm in the hospital at 3am, the doctor walks in with my results and says",
                           "Speaker 0: Anyway, next question. What tools do you use for project management?"])
        print("hanging:", story["marks"], "| transition:", story["transition"])
        assert any(m["kind"] == "hanging" for m in story["marks"]), story["marks"]
        assert story["transition"] in ("announced", "abrupt"), story["transition"]
        shallow = await run(["Speaker 1: Honestly, I almost quit the company the week before we raised the money.",
                             "Speaker 0: Cool. So what's your favorite programming language?"])
        print("followup:", shallow["followup"])
        assert shallow["followup"], "should suggest digging into almost quitting"
        agenda = await run(["Speaker 0: Hi everyone. Today we will cover three things: definitions, history, and tools.",
                            "Speaker 1: Yes. First, definitions. A robot is a machine that senses and acts."], opening=True)
        print("opening:", agenda["opening_tip"])
        cut = await run(["Speaker 1: So the reason we left Google was actually because one night my manager called me and",
                         "Speaker 0: Totally, totally. By the way, did you see the new iPhone? Crazy camera."], speaker_changed=True)
        print("cut_off:", cut["cut_off"], "| missed:", cut["missed"])
        assert cut["cut_off"] or cut["missed"], "the Google story was cut off"
        redo = await run(["Speaker 1: So we started the company in 2019, no wait, sorry, let me say that again.",
                          "Speaker 1: OK. We started the company in 2018, in a tiny apartment in Haifa."])
        print("moment:", redo["moment"])
        assert redo["moment"].get("kind") == "retake", redo["moment"]
        mem = await run(["Speaker 1: And that's how we hired our first designer.",
                         "Speaker 0: OK, totally different subject: how do you price the product, per seat or by usage?"],
                        past=["Ep 41 - Pricing models: per-seat vs usage-based, and why we switched",
                              "Ep 42 - Hiring your first engineer"])
        print("transition:", mem["transition"], "| memory:", mem.get("covered_before"))
        assert mem.get("covered_before", {}).get("episode", "").startswith("Ep 41"), mem.get("covered_before")
        fine = await run(["Speaker 1: We moved to Postgres last year, mostly for the JSON support.",
                          "Speaker 0: Interesting, and how was the migration itself, did anything break?"], speaker_changed=True)
        assert not fine["cut_off"] and not fine["missed"], (fine["cut_off"], fine["missed"])
        assert agenda["opening_tip"], "agenda-only opening should get a tip"
        print("OK")

    asyncio.run(demo())
