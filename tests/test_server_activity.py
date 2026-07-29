import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import server


class CoachActivityTests(unittest.TestCase):
    def test_coach_prompt_names_completed_blocks_from_live_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".github").mkdir()
            (repo / "data").mkdir()
            (repo / ".github" / "coach-prompt.md").write_text(
                "Coach instructions.\n", encoding="utf-8"
            )
            (repo / "data" / "state.json").write_text(
                json.dumps(
                    {
                        "today": {
                            "date": "2026-07-29",
                            "blocks": [
                                {"id": "done-1", "done": True},
                                {"id": "next-1", "done": False},
                                {"id": "done-2", "done": True},
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            prompt = server._batch_prompt(
                repo,
                {
                    "id": "coach-batch",
                    "source": "coach",
                    "routeObservationIds": [],
                    "reviewObservationIds": [],
                    "acknowledge": False,
                },
                {
                    "messageId": "message-1",
                    "text": "Change the remaining plan.",
                },
            )

        header_text = prompt.splitlines()[1]
        header = json.loads(header_text)
        self.assertEqual(header["todayDate"], "2026-07-29")
        self.assertEqual(header["completedBlockIds"], ["done-1", "done-2"])

    def test_tool_and_reasoning_events_are_public_but_raw_thinking_is_not(self):
        activity = server.CoachActivity()
        activity.start("job-1", "coach message", "claude-opus-5")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            row = {
                "type": "assistant",
                "timestamp": now,
                "message": {
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "private detailed reasoning",
                        },
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": str(repo / "data/state.json")},
                        },
                    ]
                },
            }
            with patch.object(server, "coach_activity", activity):
                server._publish_claude_row(row, repo, "job-1", 0)

                thinking = {
                    "type": "assistant",
                    "timestamp": now,
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "another private explanation",
                            }
                        ]
                    },
                }
                server._publish_claude_row(thinking, repo, "job-1", 0)

        public = activity.snapshot()["job-1"]
        labels = [event["label"] for event in public["events"]]
        self.assertIn("Read data/state.json", labels)
        self.assertIn("Working through the plan", labels)
        self.assertNotIn("private detailed reasoning", str(public))
        self.assertNotIn("another private explanation", str(public))

    def test_activity_trace_is_bounded_and_completion_is_visible(self):
        activity = server.CoachActivity(limit=3)
        activity.start("job-2", "coach message", "claude-opus-5")
        for index in range(5):
            activity.event("job-2", "read", f"Read file {index}")
        activity.finish("job-2")

        public = activity.snapshot()["job-2"]
        self.assertEqual("done", public["state"])
        self.assertIsNotNone(public["finishedAt"])
        self.assertLessEqual(len(public["events"]), 3)
        self.assertEqual("Reply saved", public["events"][-1]["label"])


if __name__ == "__main__":
    unittest.main()
