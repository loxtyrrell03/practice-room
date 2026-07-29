import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from coach_queue import CoachQueue
from practice_logs import ObservationPipeline


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class QueueLogIntegrationTests(unittest.TestCase):
    def test_daily_and_chat_transactions_preserve_both_memory_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data-repo"
            write_json(data / "data/observations.json", {"obs": []})
            write_json(data / "data/spots.json", {"spots": []})
            write_json(
                data / "data/state.json",
                {
                    "startDate": "2026-07-29",
                    "recitalDate": "2026-09-04",
                    "streak": 0,
                    "pieces": [],
                    "today": {"date": "2026-07-29", "focus": "", "blocks": []},
                    "tomorrowPreview": "",
                    "flags": [],
                    "week": {},
                },
            )
            write_json(data / "data/chat.json", {"messages": []})
            write_json(data / "data/journal.json", {"entries": []})
            memory_path = data / "memory/MEMORY.md"
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text("# Memory\n", encoding="utf-8")

            transaction_lock = threading.Lock()
            storage_lock = threading.RLock()
            pipeline = ObservationPipeline(
                data,
                storage_lock=storage_lock,
                run_lock=transaction_lock,
            )
            pipeline.migrate()
            pipeline.submit(
                {
                    "clientId": "integration-note",
                    "day": 1,
                    "blockId": "block-1",
                    "block": "Fixture",
                    "text": "daily observation",
                },
                now=datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc),
            )

            chat_started = threading.Event()
            release_chat = threading.Event()

            def chat_runner(stage, job):
                chat_started.set()
                release_chat.wait(5)
                staged_memory = Path(stage) / "memory/MEMORY.md"
                staged_memory.write_text(
                    staged_memory.read_text(encoding="utf-8") + "- chat effect\n",
                    encoding="utf-8",
                )
                chat_path = Path(stage) / "data/chat.json"
                chat = json.loads(chat_path.read_text(encoding="utf-8"))
                chat["messages"].append(
                    {
                        "role": "coach",
                        "ts": job["acceptedAt"],
                        "text": "chat reply",
                    }
                )
                chat_path.write_text(
                    json.dumps(chat, indent=2) + "\n", encoding="utf-8"
                )

            def daily_runner(stage, _batch):
                staged_memory = Path(stage) / "memory/MEMORY.md"
                staged_memory.write_text(
                    staged_memory.read_text(encoding="utf-8") + "- daily effect\n",
                    encoding="utf-8",
                )

            queue = CoachQueue(
                data,
                chat_runner,
                lock=storage_lock,
                retry_base_seconds=0,
                transaction_lock=transaction_lock,
            )
            queue.start()
            try:
                queue.accept("chat message", "integration-chat")
                self.assertTrue(chat_started.wait(2))
                daily_result = {}

                def run_daily():
                    daily_result.update(
                        pipeline.run_due(
                            daily_runner,
                            now=datetime(2026, 7, 29, 21, 0, tzinfo=timezone.utc),
                        )
                    )

                daily_thread = threading.Thread(target=run_daily)
                daily_thread.start()
                daily_thread.join(2)
                self.assertEqual(daily_result["status"], "busy")
                release_chat.set()
                self.assertTrue(queue.wait_idle(5))
                with transaction_lock:
                    pass
                daily_result.clear()
                daily_result.update(
                    pipeline.run_due(
                        daily_runner,
                        now=datetime(2026, 7, 29, 21, 0, tzinfo=timezone.utc),
                    )
                )
            finally:
                release_chat.set()
                queue.stop()

            memory = memory_path.read_text(encoding="utf-8")
            observations = json.loads(
                (data / "data/observations.json").read_text(encoding="utf-8")
            )["obs"]
            self.assertEqual(daily_result["status"], "processed")
            self.assertIn("- chat effect", memory)
            self.assertIn("- daily effect", memory)
            self.assertEqual(observations[0]["status"], "processed")
            self.assertEqual(
                len(
                    [
                        message
                        for message in json.loads(
                            (data / "data/chat.json").read_text(encoding="utf-8")
                        )["messages"]
                        if message["role"] == "coach"
                    ]
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
