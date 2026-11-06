"""One live episode: the transcript so far, and which of the director's findings are worth showing."""

import time

from backend.flow import switches_per_min


def clock(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


class Session:
    def __init__(self, past: list[str]):
        self.past = past  # past episodes, for "covered before"
        self.lines: list[str] = []  # "Speaker N: text", one paragraph per turn
        self.timed: list[str] = []  # "[mm:ss] Speaker N: phrase" per final phrase, for chapters
        self.flagged: list[str] = []  # terms already explained
        self.open_marks: list[dict] = []  # postponed topics not yet covered: {id, topic}
        self.next_mark_id = 1
        self.marks_total = 0
        self.transitions: list[str] = []  # kinds of topic changes
        self.memory_shown: set[str] = set()
        self.moments: list[dict] = []  # edit list: {"at", "kind", "note"}
        self.tip_counts: dict[str, int] = {}  # for the scorecard
        self.last_sent: dict[str, float] = {}  # finding kind -> when it was last shown
        self.opening_due = True

    def due(self, kind: str, every: float) -> bool:
        """True, and restarts the cooldown, if nothing of this kind was shown in the last `every` seconds."""
        now = time.monotonic()
        if now - self.last_sent.get(kind, -1e9) < every:
            return False
        self.last_sent[kind] = now
        return True

    def add_final(self, speaker: int | None, text: str, start: float) -> bool:
        """Adds a final phrase. True if it starts a new speaker's turn."""
        prefix = f"Speaker {speaker}: "
        self.timed.append(f"[{clock(start)}] {prefix}{text}")
        if self.lines and self.lines[-1].startswith(prefix):  # same speaker: one paragraph, like the screen
            self.lines[-1] += " " + text
            return False
        self.lines.append(prefix + text)
        return len(self.lines) > 1

    def director_input(self, new: str, spoken: float, changed: bool) -> dict:
        opening = self.opening_due and spoken >= 300  # once, around minute 5
        if opening:
            self.opening_due = False
        return {"lines": list(self.lines) if opening else self.lines[-5:], "new": new,
                "flagged": list(self.flagged), "open_marks": list(self.open_marks),
                "opening": opening, "speaker_changed": changed, "past": self.past}

    def moment(self, at: float, kind: str, note: str) -> dict:
        m = {"at": clock(at), "kind": kind, "note": note}
        self.moments.append(m)
        return {"type": "moment", **m}

    def review(self, result: dict, at: float) -> list[dict]:
        """The director's findings that are new and not on cooldown, as messages for the browser."""
        out = []

        def tip(kind: str, term: str, note: str, every: float) -> bool:
            if not self.due(kind, every):  # one cooldown per kind so craft tips stay rare
                return False
            self.tip_counts[kind] = self.tip_counts.get(kind, 0) + 1
            out.append({"type": "tip", "kind": kind, "term": term, "note": note})
            return True

        if result.get("opening_tip"):
            tip("opening", "Opening", result["opening_tip"], 0)
        if result["transition"]:
            self.transitions.append(result["transition"])
            # Good shows announce ~1 in 3 topic changes, flat ones ~2 in 3: only nudge on a pattern.
            if result["transition"] == "announced" and self.transitions[-3:].count("announced") >= 2:
                tip("transition", "Transition", "Bridge it: link the new topic to what was just said.", 300)
        covered = result.get("covered_before")
        if covered and covered["episode"] not in self.memory_shown:
            if tip("memory", "Covered before", f"{covered['episode']}: {covered['note']}", 120):
                self.memory_shown.add(covered["episode"])
        if result["followup"]:
            tip("followup", "Dig in", result["followup"], 120)
        if result["cut_off"]:
            tip("let_finish", "Let them finish", "They were mid-thought. Let them land it.", 300)

        moment = result["moment"]
        if moment and (moment["kind"] not in ("clip", "sponsor") or self.due(moment["kind"], 60)):  # one sponsor read = one mark
            out.append(self.moment(at, moment["kind"], moment["note"]))

        # Overlapping director calls can find the same term twice, so dedupe by name, plural/prefix forms too.
        for t in result["tips"]:
            term = t["term"].lower()
            if any(term in f.lower() or f.lower() in term for f in self.flagged if len(f) > 2):
                continue
            if not self.due("term", 15):  # jargon-heavy shows flood the margin
                continue
            self.flagged.append(t["term"])
            out.append({"type": "tip", **t})

        marks = list(result["marks"])
        if result["missed"] and self.due("their_point", 180):
            marks.append({**result["missed"], "kind": "their_point"})  # shown and crossed off like other marks
        for mark in marks:
            if mark["topic"].lower() in (m["topic"].lower() for m in self.open_marks):
                continue
            if mark.get("kind") == "hanging" and not self.due("hanging", 120):  # the AI over-reports these
                continue
            mark = {"id": self.next_mark_id, **mark}
            self.next_mark_id += 1
            self.open_marks.append(mark)
            self.marks_total += 1
            out.append({"type": "mark", **mark})

        for mark_id in result["resolved"]:
            if any(m["id"] == mark_id for m in self.open_marks):
                self.open_marks = [m for m in self.open_marks if m["id"] != mark_id]
                out.append({"type": "resolved", "id": mark_id})
        return out

    def stats(self, words: list[tuple[float, float, int]], melody: float | None) -> dict:
        """What the live checkers logged, for the after-episode scorecard."""
        talk: dict[int, float] = {}
        for start, end, speaker in words:
            talk[speaker] = talk.get(speaker, 0) + end - start
        total = sum(talk.values()) or 1
        return {"melody": melody, "switches": switches_per_min(words) if len(words) > 1 else None,
                "transitions": self.transitions,
                "talk": {k: v / total for k, v in talk.items() if v / total >= 0.05},  # drop phantom speakers
                "marks": self.marks_total, "open_marks": len(self.open_marks), "tips": self.tip_counts}
