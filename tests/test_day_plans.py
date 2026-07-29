import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from day_plans import (
    DayPlanError,
    is_debrief_message,
    promote_due_plan,
    validate_coach_day_change,
)


def ready_plan(date="2026-07-30"):
    return {
        "date": date,
        "day": 2,
        "status": "ready",
        "generatedAt": "2026-07-29T19:20:51Z",
        "basedOnDate": "2026-07-29",
        "focus": "Specific work.",
        "blocks": [
            {
                "id": "bach-b16",
                "title": "Bach Fugue b.16",
                "mins": 10,
                "flag": "urgent",
                "done": False,
                "steps": [
                    {
                        "lead": "6 reps · Single-voice repair",
                        "text": (
                            "Play b.16 at 40–50%; release the substitution "
                            "without tightening."
                        ),
                    },
                    {
                        "lead": "Pass when",
                        "text": "One cold repeat stays loose and rhythmically even.",
                    },
                ],
                "why": "The logged tension is local to the finger change.",
                "logRefs": [
                    {
                        "observationId": "obs-1",
                        "note": "Day 1 · Bach b.16 tension.",
                    }
                ],
            }
        ],
        "sourceObservationIds": ["obs-1"],
        "deferredLogs": [],
    }


class DayPlanTests(unittest.TestCase):
    def test_natural_end_of_day_message_is_a_debrief(self):
        text = (
            "I’m finished with practice now. That’s my debrief for the day. "
            "Generate the plan for tomorrow."
        )
        self.assertTrue(is_debrief_message(text))
        self.assertTrue(is_debrief_message("Debrief: four hours, no pain."))
        self.assertTrue(is_debrief_message("I’m finished with practice today."))
        self.assertFalse(is_debrief_message("What should tomorrow look like?"))

    def test_future_plan_cannot_replace_active_today(self):
        before_state = {
            "today": {"date": "2026-07-29", "focus": "Day 1", "blocks": []}
        }
        after_state = {
            "today": {"date": "2026-07-30", "focus": "Day 2", "blocks": []}
        }
        with self.assertRaisesRegex(DayPlanError, "future plan"):
            validate_coach_day_change(
                before_state=before_state,
                after_state=after_state,
                before_plans={"version": 1, "plans": []},
                after_plans={"version": 1, "plans": []},
                job={"acceptedAt": "2026-07-29T19:20:51Z"},
                batch={"routeObservationIds": [], "reviewObservationIds": []},
            )

    def test_new_ready_plan_must_account_for_every_named_log(self):
        with self.assertRaisesRegex(DayPlanError, "obs-2"):
            validate_coach_day_change(
                before_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                after_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                before_plans={"version": 1, "plans": []},
                after_plans={"version": 1, "plans": [ready_plan()]},
                job={"acceptedAt": "2026-07-29T19:20:51Z"},
                batch={
                    "routeObservationIds": ["obs-1"],
                    "reviewObservationIds": ["obs-2"],
                },
            )

    def test_ready_plan_rejects_paragraph_instead_of_bullets(self):
        plan = ready_plan()
        block = plan["blocks"][0]
        block.pop("steps")
        block["detail"] = (
            "Single voice at 40–50%, six repetitions, release on the "
            "substitution, then verify once cold."
        )
        with self.assertRaisesRegex(DayPlanError, "instruction bullets"):
            validate_coach_day_change(
                before_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                after_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                before_plans={"version": 1, "plans": []},
                after_plans={"version": 1, "plans": [plan]},
                job={"acceptedAt": "2026-07-29T19:20:51Z"},
                batch={"routeObservationIds": [], "reviewObservationIds": []},
            )

    def test_ready_plan_requires_bold_technique_and_dose_lead(self):
        plan = ready_plan()
        plan["blocks"][0]["steps"][0]["lead"] = "Single-voice repair"
        with self.assertRaisesRegex(DayPlanError, "duration/reps and technique"):
            validate_coach_day_change(
                before_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                after_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                before_plans={"version": 1, "plans": []},
                after_plans={"version": 1, "plans": [plan]},
                job={"acceptedAt": "2026-07-29T19:20:51Z"},
                batch={"routeObservationIds": [], "reviewObservationIds": []},
            )

    def test_rough_plan_rejects_preview_prose(self):
        with self.assertRaisesRegex(DayPlanError, "outline bullets"):
            validate_coach_day_change(
                before_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                after_state={
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []}
                },
                before_plans={"version": 1, "plans": []},
                after_plans={
                    "version": 1,
                    "plans": [
                        {
                            "date": "2026-07-31",
                            "day": 3,
                            "status": "rough",
                            "preview": "A long paragraph about what may happen.",
                        }
                    ],
                },
                job={"acceptedAt": "2026-07-29T19:20:51Z"},
                batch={"routeObservationIds": [], "reviewObservationIds": []},
            )

    def test_ready_plan_promotes_only_when_its_date_begins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            state_path = root / "data/state.json"
            plans_path = root / "data/day-plans.json"
            state_path.write_text(
                json.dumps(
                    {
                        "pieces": [],
                        "today": {
                            "date": "2026-07-29",
                            "focus": "Day 1",
                            "blocks": [{"id": "old", "done": True}],
                        },
                    }
                ),
                encoding="utf-8",
            )
            plans_path.write_text(
                json.dumps({"version": 1, "plans": [ready_plan()]}),
                encoding="utf-8",
            )

            early = promote_due_plan(
                root, datetime(2026, 7, 29, 22, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(early["status"], "current")
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))["today"]["date"],
                "2026-07-29",
            )

            promoted = promote_due_plan(
                root, datetime(2026, 7, 30, 7, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(promoted["status"], "promoted")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["today"]["date"], "2026-07-30")
            self.assertEqual(state["today"]["blocks"][0]["id"], "bach-b16")
            self.assertFalse(state["today"]["blocks"][0]["done"])


if __name__ == "__main__":
    unittest.main()
