import json
import tempfile
import unittest
from pathlib import Path

from coach_queue import CoachQueue
from day_plans import local_date_from_iso
from server import ClaudeCoachRunner


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class RepertoireQueueIntegrationTests(unittest.TestCase):
    def test_confirmed_drop_commits_state_plan_audit_and_reply_together(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data-repo"
            write_json(
                data / "data/state.json",
                {
                    "startDate": "2026-07-29",
                    "recitalDate": "2026-09-04",
                    "streak": 0,
                    "pieces": [
                        {
                            "id": "alpha",
                            "title": "Composer — Alpha Sonata",
                            "short": "Alpha",
                        },
                        {
                            "id": "beta",
                            "title": "Composer — Beta Prelude",
                            "short": "Beta",
                        },
                    ],
                    "today": {
                        "date": "2026-07-29",
                        "focus": "Alpha then Beta",
                        "blocks": [
                            {
                                "id": "alpha-work",
                                "title": "Alpha — repair",
                                "mins": 20,
                                "done": False,
                            },
                            {
                                "id": "beta-work",
                                "title": "Beta — repair",
                                "mins": 20,
                                "done": False,
                            },
                        ],
                    },
                    "tomorrowPreview": "Alpha then Beta",
                    "flags": [],
                    "week": {
                        "goals": ["Alpha stable", "Beta stable"],
                        "gate": ["Alpha cold", "Beta cold"],
                    },
                },
            )
            write_json(data / "data/chat.json", {"messages": []})
            write_json(data / "data/journal.json", {"entries": []})
            write_json(data / "data/spots.json", {"spots": []})
            write_json(data / "data/observations.json", {"version": 2, "obs": []})
            write_json(
                data / "data/observation-jobs.json",
                {"version": 1, "batches": {}},
            )
            write_json(
                data / "data/repertoire-changes.json",
                {"version": 1, "pending": [], "changes": []},
            )
            (data / "context").mkdir()
            (data / "memory").mkdir()
            (data / "context/plan.md").write_text(
                "# Plan\nAlpha 20 min\nBeta 20 min\n", encoding="utf-8"
            )
            (data / "context/repertoire.md").write_text(
                "# Repertoire\nAlpha\nBeta\n", encoding="utf-8"
            )
            (data / "memory/MEMORY.md").write_text(
                "# Memory\n", encoding="utf-8"
            )

            def fake_coach(stage, _batch, job):
                self.assertEqual(
                    job["repertoireDirective"]["kind"], "drop"
                )
                state_path = Path(stage) / "data/state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    [piece["id"] for piece in state["pieces"]], ["beta"]
                )
                state["today"]["focus"] = "Beta first"
                state["tomorrowPreview"] = "Beta"
                state["week"] = {
                    "goals": ["Beta stable"],
                    "gate": ["Beta cold"],
                }
                write_json(state_path, state)
                (Path(stage) / "context/plan.md").write_text(
                    "# Remaining plan\nBeta 40 min\n", encoding="utf-8"
                )
                (Path(stage) / "context/repertoire.md").write_text(
                    "# Repertoire\nBeta\n", encoding="utf-8"
                )
                (Path(stage) / "memory/MEMORY.md").write_text(
                    "# Memory\n- "
                    + local_date_from_iso(job["acceptedAt"])
                    + ": Alpha dropped.\n",
                    encoding="utf-8",
                )
                chat_path = Path(stage) / "data/chat.json"
                chat = json.loads(chat_path.read_text(encoding="utf-8"))
                chat["messages"].append(
                    {
                        "role": "coach",
                        "ts": job["acceptedAt"],
                        "text": "Alpha has been removed; its time moves to Beta.",
                    }
                )
                write_json(chat_path, chat)

            queue = CoachQueue(
                data,
                ClaudeCoachRunner(coach_run=fake_coach),
                retry_base_seconds=0,
            )
            queue.accept("I'm dropping Alpha.", "drop-alpha")
            queue.drain_until_idle(ignore_retry_time=True, max_steps=1)
            queued = queue.snapshot()["jobs"][0]
            self.assertEqual("done", queued["state"], queued["lastError"])

            state = json.loads(
                (data / "data/state.json").read_text(encoding="utf-8")
            )
            ledger = json.loads(
                (data / "data/repertoire-changes.json").read_text(
                    encoding="utf-8"
                )
            )
            messages = json.loads(
                (data / "data/chat.json").read_text(encoding="utf-8")
            )["messages"]
            self.assertEqual([piece["id"] for piece in state["pieces"]], ["beta"])
            self.assertEqual(ledger["changes"][0]["status"], "planned")
            self.assertNotIn(
                "Alpha", (data / "context/plan.md").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [message["role"] for message in messages], ["user", "coach"]
            )
            self.assertEqual(queue.snapshot()["jobs"][0]["state"], "done")


if __name__ == "__main__":
    unittest.main()
