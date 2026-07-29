import copy
import json
import tempfile
import unittest
from pathlib import Path

from repertoire_changes import (
    RepertoireChangeManager,
    RepertoireConsistencyError,
    render_repertoire_prompt,
)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class RepertoireChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "data-repo"
        pieces = [
            {
                "id": "alpha",
                "title": "Composer — Alpha Sonata",
                "short": "Alpha",
                "security": 60,
                "tempoPct": 80,
                "lastCold": None,
                "attention": "steady",
                "note": "Active.",
            },
            {
                "id": "beta",
                "title": "Composer — Beta Prelude",
                "short": "Beta",
                "security": 40,
                "tempoPct": 60,
                "lastCold": None,
                "attention": "focus",
                "note": "Active.",
            },
        ]
        state = {
            "startDate": "2026-07-29",
            "recitalDate": "2026-09-04",
            "streak": 1,
            "pieces": pieces,
            "today": {
                "date": "2026-07-29",
                "focus": "Alpha first.",
                "blocks": [
                    {
                        "id": "alpha-done",
                        "title": "Alpha — completed",
                        "mins": 10,
                        "done": True,
                    },
                    {
                        "id": "alpha-next",
                        "title": "Alpha — repair",
                        "mins": 20,
                        "done": False,
                    },
                    {
                        "id": "beta-next",
                        "title": "Beta — repair",
                        "mins": 20,
                        "done": False,
                    },
                ],
            },
            "tomorrowPreview": "Alpha and Beta.",
            "flags": [],
            "week": {
                "goals": ["Alpha stable", "Beta stable"],
                "gate": ["Alpha cold", "Beta cold"],
            },
        }
        write_json(self.repo / "data/state.json", state)
        write_json(
            self.repo / "data/repertoire-changes.json",
            {"version": 1, "pending": [], "changes": []},
        )
        write_json(self.repo / "data/chat.json", {"messages": []})
        write_json(
            self.repo / "data/journal.json",
            {"entries": [{"date": "2026-07-28", "day": 0, "title": "Before"}]},
        )
        write_json(
            self.repo / "data/spots.json",
            {"spots": [{"id": "alpha-b1", "piece": "alpha", "status": "fixed"}]},
        )
        (self.repo / "context").mkdir(parents=True)
        (self.repo / "memory").mkdir()
        (self.repo / "context/plan.md").write_text(
            "# Plan\nAlpha: 20 min.\nBeta: 20 min.\n", encoding="utf-8"
        )
        (self.repo / "context/repertoire.md").write_text(
            "# Repertoire\nAlpha\nBeta\n", encoding="utf-8"
        )
        (self.repo / "memory/MEMORY.md").write_text(
            "# Memory\n", encoding="utf-8"
        )
        self.manager = RepertoireChangeManager(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, text, message_id="message-1"):
        return {
            "text": text,
            "messageId": message_id,
            "acceptedAt": "2026-07-29T10:00:00+01:00",
        }

    def state(self):
        return json.loads(
            (self.repo / "data/state.json").read_text(encoding="utf-8")
        )

    def ledger(self):
        return json.loads(
            (self.repo / "data/repertoire-changes.json").read_text(
                encoding="utf-8"
            )
        )

    def coach_reply(self, text):
        chat = json.loads(
            (self.repo / "data/chat.json").read_text(encoding="utf-8")
        )
        chat["messages"].append(
            {
                "role": "coach",
                "ts": "2026-07-29T10:00:01+01:00",
                "text": text,
            }
        )
        write_json(self.repo / "data/chat.json", chat)

    def finish_drop(self):
        state = self.state()
        state["today"]["focus"] = "Beta first."
        state["tomorrowPreview"] = "Beta."
        state["week"] = {
            "goals": ["Beta stable"],
            "gate": ["Beta cold"],
        }
        write_json(self.repo / "data/state.json", state)
        (self.repo / "context/plan.md").write_text(
            "# Remaining plan\nBeta: 40 min after programme cut.\n",
            encoding="utf-8",
        )
        (self.repo / "context/repertoire.md").write_text(
            "# Working repertoire\nBeta\n", encoding="utf-8"
        )
        (self.repo / "memory/MEMORY.md").write_text(
            "# Memory\n- 2026-07-29: Alpha was dropped from the programme.\n",
            encoding="utf-8",
        )
        self.coach_reply("Alpha has been removed. Its 20 minutes move to Beta.")

    def finish_add(self, directive):
        state = self.state()
        added = next(
            piece
            for piece in state["pieces"]
            if piece["id"] == directive["piece"]["id"]
        )
        added["planning"] = {
            "learningTimeline": "Text secure by Day 8.",
            "memorisationTimeline": "Memory sections Days 2–10.",
            "performanceExposure": "First filmed run Day 14; then twice weekly.",
            "physicalRiskConstraints": "Ten-minute doses; stop on pain.",
            "cutPriority": "Review after existing optional item.",
            "dailyMinutes": 35,
            "gateTargets": ["Day 7: first half stable", "Day 14: filmed run"],
            "prescriptions": ["Overlapping two-bar chunks, 8 minutes each."],
        }
        state["today"]["blocks"].insert(
            0,
            {
                "id": f"{added['id']}-learn",
                "title": f"{added['title']} — first learning block",
                "mins": 35,
                "done": False,
            },
        )
        state["week"]["goals"].append(f"{added['title']}: first half secure")
        state["week"]["gate"].append(f"{added['title']}: cold starts pass")
        write_json(self.repo / "data/state.json", state)
        title = added["title"]
        version = added["version"]
        (self.repo / "context/plan.md").write_text(
            f"# Recomputed plan\n{title}: 35 min/day, filmed Day 14.\n",
            encoding="utf-8",
        )
        (self.repo / "context/repertoire.md").write_text(
            f"# Working repertoire\nAlpha\nBeta\n{title} — {version}\n",
            encoding="utf-8",
        )
        (self.repo / "memory/MEMORY.md").write_text(
            f"# Memory\n- 2026-07-29: added {title}; remaining plan rebuilt.\n",
            encoding="utf-8",
        )
        self.coach_reply(
            f"Added {title} and integrated it at 35 minutes/day; "
            "the remaining plan and gates are replanned."
        )

    def test_confirmed_drop_is_atomic_and_preserves_completed_history(self):
        directive = self.manager.prepare(self.job("I'm dropping Alpha."))

        self.assertEqual(directive["kind"], "drop")
        state = self.state()
        self.assertEqual([piece["id"] for piece in state["pieces"]], ["beta"])
        self.assertEqual(
            [block["id"] for block in state["today"]["blocks"]],
            ["alpha-done", "beta-next"],
        )
        self.assertEqual(len(self.ledger()["changes"]), 1)

        self.finish_drop()
        self.manager.validate(directive)

        self.assertEqual(self.ledger()["changes"][0]["status"], "planned")
        journal = json.loads(
            (self.repo / "data/journal.json").read_text(encoding="utf-8")
        )
        spots = json.loads(
            (self.repo / "data/spots.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(journal["entries"]), 1)
        self.assertEqual(spots["spots"][0]["id"], "alpha-b1")

    def test_confirmed_add_requires_supplied_facts_and_first_class_plan(self):
        message = """I'm adding a piece.
Title: Composer — Gamma Etude
Version: original piano version
Current state: notes learned, not memorised
Duration: 4.5 minutes
Deadline: 2026-09-04
Target tempo: quarter note = 120 BPM"""
        directive = self.manager.prepare(self.job(message))

        self.assertEqual(directive["kind"], "add")
        added = self.state()["pieces"][-1]
        self.assertEqual(added["title"], "Composer — Gamma Etude")
        self.assertEqual(added["durationMinutes"], 4.5)
        self.assertIsNone(added["security"])
        self.assertIsNone(added["tempoPct"])

        self.finish_add(directive)
        self.manager.validate(directive)
        self.assertEqual(self.ledger()["changes"][0]["status"], "planned")

    def test_ambiguous_language_only_asks_for_confirmation(self):
        before = copy.deepcopy(self.state())
        directive = self.manager.prepare(
            self.job("I'm thinking about dropping Alpha.")
        )

        self.assertEqual(directive["kind"], "clarify")
        self.assertEqual(directive["reason"], "ambiguous-drop")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.ledger()["changes"], [])
        self.coach_reply("Are you definitely dropping Alpha, or weighing it?")
        self.manager.validate(directive)

    def test_unknown_drop_never_mutates_the_working_repertoire(self):
        before = copy.deepcopy(self.state())
        directive = self.manager.prepare(self.job("Remove Delta from my programme."))

        self.assertEqual(directive["kind"], "clarify")
        self.assertEqual(directive["reason"], "unknown-piece")
        self.assertEqual(self.state(), before)
        self.coach_reply("Which working-repertoire piece do you mean: Alpha or Beta?")
        self.manager.validate(directive)

    def test_ambiguous_instruction_rejects_silent_future_replanning(self):
        directive = self.manager.prepare(
            self.job("Maybe I should drop Alpha.")
        )
        state = self.state()
        state["today"]["blocks"] = [
            block
            for block in state["today"]["blocks"]
            if block["id"] != "alpha-next"
        ]
        write_json(self.repo / "data/state.json", state)
        self.coach_reply("Are you definitely dropping Alpha?")

        with self.assertRaisesRegex(
            RepertoireConsistencyError, "must not change future practice planning"
        ):
            self.manager.validate(directive)

    def test_repeated_drop_is_idempotent(self):
        first = self.manager.prepare(self.job("I'm dropping Alpha.", "drop-1"))
        self.finish_drop()
        self.manager.validate(first)
        count = len(self.ledger()["changes"])

        repeated = self.manager.prepare(
            self.job("I'm dropping Alpha.", "drop-2")
        )
        self.assertEqual(repeated["kind"], "noop")
        self.coach_reply("Alpha is already removed; no further change was made.")
        self.manager.validate(repeated)
        self.assertEqual(len(self.ledger()["changes"]), count)

    def test_incomplete_addition_persists_only_a_pending_request(self):
        directive = self.manager.prepare(
            self.job("I'm adding Composer — Gamma Etude.")
        )

        self.assertEqual(directive["kind"], "clarify")
        self.assertEqual(
            directive["missing"],
            [
                "version",
                "currentState",
                "durationMinutes",
                "deadline",
                "targetTempo",
            ],
        )
        self.assertEqual(
            [piece["id"] for piece in self.state()["pieces"]], ["alpha", "beta"]
        )
        self.assertEqual(self.ledger()["changes"], [])
        self.assertEqual(len(self.ledger()["pending"]), 1)
        prompt = render_repertoire_prompt(self.manager.public_directive(directive))
        self.assertIn("Ask only for the fields in `missing`", prompt)
        self.coach_reply(
            "What version, current state, duration, deadline, and target tempo "
            "(BPM plus beat unit) should I use?"
        )
        self.manager.validate(directive)

    def test_pending_addition_can_complete_without_repeating_the_title(self):
        first = self.manager.prepare(
            self.job("I'm adding Composer — Gamma Etude.", "add-start")
        )
        self.coach_reply(
            "What version, current state, duration, deadline, and target tempo?"
        )
        self.manager.validate(first)

        continuation = """Version: original piano version
Current state: notes learned, not memorised
Duration: 4.5 minutes
Deadline: 2026-09-04
Target tempo: quarter note = 120 BPM"""
        complete = self.manager.prepare(
            self.job(continuation, "add-complete")
        )

        self.assertEqual(complete["kind"], "add")
        self.assertEqual(self.ledger()["pending"], [])
        self.finish_add(complete)
        self.manager.validate(complete)

    def test_inconsistent_added_piece_plan_is_rejected(self):
        message = """Add this to my programme.
Title: Composer — Gamma Etude
Version: original piano version
Current state: notes learned, not memorised
Duration: 4.5 minutes
Deadline: 2026-09-04
Target tempo: quarter note = 120 BPM"""
        directive = self.manager.prepare(self.job(message))
        added = directive["piece"]
        (self.repo / "context/plan.md").write_text(
            f"# Plan\n{added['title']}\n", encoding="utf-8"
        )
        (self.repo / "context/repertoire.md").write_text(
            f"# Repertoire\n{added['title']} — {added['version']}\n",
            encoding="utf-8",
        )
        (self.repo / "memory/MEMORY.md").write_text(
            f"2026-07-29: added {added['title']}\n", encoding="utf-8"
        )
        self.coach_reply(f"Added {added['title']}.")

        with self.assertRaisesRegex(
            RepertoireConsistencyError, "first-class planning record"
        ):
            self.manager.validate(directive)


if __name__ == "__main__":
    unittest.main()
