import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from coach_queue import CoachQueue


class FakeRunner:
    def __init__(self, *, block_first=False, fail_first=False, fail_after_reply=False):
        self.calls = []
        self.block_first = block_first
        self.fail_first = fail_first
        self.fail_after_reply = fail_after_reply
        self.started = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, stage, job):
        with self.lock:
            self.calls.append(job["messageId"])
            call_number = len(self.calls)
        if call_number == 1:
            self.started.set()
            if self.block_first:
                self.release.wait(5)
            if self.fail_first and not self.fail_after_reply:
                raise RuntimeError("fake coach unavailable")

        path = Path(stage) / "data/chat.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["messages"].append(
            {
                "role": "coach",
                "ts": job["acceptedAt"],
                "text": f"reply to {job['text']}",
            }
        )
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        if call_number == 1 and self.fail_after_reply:
            raise RuntimeError("fake crash after writing staged reply")


class PrepareOnlyQueue(CoachQueue):
    """Simulate a process stop after the prepared result is durable."""

    def _apply_prepared(self, job_id):
        self.prepared_job_id = job_id
        return False


class CoachQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data-repo"
        (self.data / "data").mkdir(parents=True)
        (self.data / "memory").mkdir()
        (self.data / "context").mkdir()
        fixtures = {
            "data/chat.json": {"messages": []},
            "data/state.json": {"startDate": "2026-07-29", "recitalDate": "2026-09-04"},
            "data/weekly-plan.json": {
                "updated": "2026-07-29",
                "phases": [{"id": "week-1", "title": "Triage"}],
            },
            "data/journal.json": {"entries": []},
            "data/spots.json": {"spots": []},
            "data/observations.json": {"obs": []},
            "data/observation-jobs.json": {"version": 1, "batches": {}},
            "data/repertoire-changes.json": {
                "version": 1,
                "pending": [],
                "changes": [],
            },
        }
        for rel, value in fixtures.items():
            (self.data / rel).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        (self.data / "memory/MEMORY.md").write_text("# Memory\n", encoding="utf-8")
        (self.data / "context/plan.md").write_text("# Plan\n", encoding="utf-8")
        (self.data / "context/repertoire.md").write_text(
            "# Repertoire\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def messages(self):
        return json.loads((self.data / "data/chat.json").read_text(encoding="utf-8"))["messages"]

    def test_original_race_drains_message_accepted_while_running(self):
        runner = FakeRunner(block_first=True)
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.start()
        try:
            first = queue.accept("first", "request-1")
            self.assertTrue(runner.started.wait(2))
            second = queue.accept("second", "request-2")
            self.assertEqual(queue.snapshot()["processing"], 1)
            self.assertEqual(queue.snapshot()["pending"], 1)
            runner.release.set()
            self.assertTrue(queue.wait_idle(5))
        finally:
            runner.release.set()
            queue.stop()

        self.assertEqual(runner.calls, [first["messageId"], second["messageId"]])
        self.assertEqual(
            [(m["role"], m["text"]) for m in self.messages()],
            [
                ("user", "first"),
                ("coach", "reply to first"),
                ("user", "second"),
                ("coach", "reply to second"),
            ],
        )

    def test_rapid_submissions_are_processed_in_acceptance_order(self):
        runner = FakeRunner()
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        accepted = [queue.accept(f"message {i}", f"request-{i}") for i in range(12)]

        queue.drain_until_idle(ignore_retry_time=True)

        self.assertEqual(runner.calls, [item["messageId"] for item in accepted])
        replies = [m for m in self.messages() if m["role"] == "coach"]
        self.assertEqual([m["text"] for m in replies], [f"reply to message {i}" for i in range(12)])

    def test_model_selection_is_durable_and_idempotent(self):
        queue = CoachQueue(self.data, FakeRunner(), retry_base_seconds=0)
        selection = {
            "provider": "openai",
            "model": "gpt-5.6-terra",
            "effort": "xhigh",
        }
        first = queue.accept("Use this model", "model-request", selection)
        again = queue.accept("Use this model", "model-request", selection)

        self.assertEqual(selection, first["selection"])
        self.assertEqual(first["id"], again["id"])
        with self.assertRaisesRegex(ValueError, "different model"):
            queue.accept(
                "Use this model",
                "model-request",
                {**selection, "effort": "low"},
            )

    def test_rejects_unsupported_model_before_durable_acceptance(self):
        queue = CoachQueue(self.data, FakeRunner(), retry_base_seconds=0)
        with self.assertRaisesRegex(ValueError, "unsupported coach model"):
            queue.accept(
                "unknown",
                "unknown-model",
                {"provider": "openai", "model": "invented", "effort": "high"},
            )
        self.assertEqual([], queue.snapshot()["jobs"])

    def test_weekly_plan_update_is_committed_with_coach_reply(self):
        def runner(stage, job):
            plan_path = Path(stage) / "data/weekly-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["updated"] = "2026-07-30"
            plan["phases"].append({"id": "week-2", "title": "Integration"})
            plan_path.write_text(
                json.dumps(plan, indent=2) + "\n", encoding="utf-8"
            )
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat["messages"].append(
                {
                    "role": "coach",
                    "ts": job["acceptedAt"],
                    "text": "Future phases updated.",
                }
            )
            chat_path.write_text(
                json.dumps(chat, indent=2) + "\n", encoding="utf-8"
            )

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.accept("rebuild the plan", "weekly-plan-request")
        queue.drain_until_idle(ignore_retry_time=True)

        plan = json.loads(
            (self.data / "data/weekly-plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plan["updated"], "2026-07-30")
        self.assertEqual([phase["id"] for phase in plan["phases"]], ["week-1", "week-2"])
        self.assertEqual(self.messages()[-1]["text"], "Future phases updated.")

    def test_concurrent_phone_and_laptop_intake_is_lossless(self):
        runner = FakeRunner()
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)

        def send(i):
            return queue.accept(f"concurrent {i}", f"device-request-{i}")

        with ThreadPoolExecutor(max_workers=16) as pool:
            accepted = list(pool.map(send, range(40)))
        queue.drain_until_idle(ignore_retry_time=True)

        jobs = sorted(queue.snapshot()["jobs"], key=lambda item: item["sequence"])
        self.assertEqual(len(jobs), 40)
        self.assertEqual(len({item["messageId"] for item in accepted}), 40)
        self.assertEqual(runner.calls, [item["messageId"] for item in jobs])
        self.assertEqual(len([m for m in self.messages() if m["role"] == "coach"]), 40)

    def test_failure_is_visible_and_retries_without_skipping_fifo_head(self):
        runner = FakeRunner(fail_first=True)
        queue = CoachQueue(self.data, runner, retry_base_seconds=60)
        first = queue.accept("first", "request-1")
        second = queue.accept("second", "request-2")

        self.assertTrue(queue.drain_once())
        snap = queue.snapshot()
        self.assertEqual(snap["failed"], 1)
        self.assertIn("fake coach unavailable", snap["jobs"][0]["lastError"])
        self.assertFalse(queue.drain_once())

        queue.drain_until_idle(ignore_retry_time=True)
        self.assertEqual(
            runner.calls,
            [first["messageId"], first["messageId"], second["messageId"]],
        )
        self.assertEqual(queue.snapshot()["pending"], 0)

    def test_staged_history_rewrites_are_discarded_while_reply_is_kept(self):
        def runner(stage, job):
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            original_count = len(chat["messages"])
            chat["messages"][0]["text"] = "coach tried to rewrite history"
            chat["messages"].append(
                {
                    "role": "coach",
                    "ts": job["acceptedAt"],
                    "text": "The new reply.",
                }
            )
            self.assertEqual(len(chat["messages"]), original_count + 1)
            chat_path.write_text(
                json.dumps(chat, indent=2) + "\n", encoding="utf-8"
            )

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.accept("Keep history canonical", "rewrite-discarded")
        queue.drain_until_idle(ignore_retry_time=True)

        messages = self.messages()
        self.assertEqual(messages[0]["text"], "Keep history canonical")
        self.assertEqual(messages[-1]["text"], "The new reply.")
        self.assertEqual(queue.snapshot()["pending"], 0)

    def test_processing_job_is_recovered_after_restart(self):
        runner = FakeRunner()
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        accepted = queue.accept("survive restart", "request-restart")
        saved = json.loads(queue.queue_path.read_text(encoding="utf-8"))
        saved["jobs"][0]["state"] = "processing"
        queue.queue_path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")

        restarted = CoachQueue(self.data, runner, retry_base_seconds=0)
        self.assertEqual(restarted.snapshot()["jobs"][0]["state"], "queued")
        restarted.drain_until_idle(ignore_retry_time=True)

        self.assertEqual(runner.calls, [accepted["messageId"]])
        self.assertEqual(len([m for m in self.messages() if m["role"] == "coach"]), 1)

    def test_request_id_and_reply_replay_cannot_duplicate(self):
        runner = FakeRunner()
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: queue.accept("same send", "stable-request-id"), range(20)))
        self.assertEqual(len({item["messageId"] for item in results}), 1)
        queue.drain_until_idle(ignore_retry_time=True)
        self.assertEqual(len(runner.calls), 1)

        # Simulate a stop after the reply became visible but before the durable
        # job state was recorded as done.
        saved = json.loads(queue.queue_path.read_text(encoding="utf-8"))
        saved["jobs"][0]["state"] = "processing"
        saved["jobs"][0]["completedAt"] = None
        queue.queue_path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        restarted = CoachQueue(self.data, runner, retry_base_seconds=0)
        restarted.drain_until_idle(ignore_retry_time=True)

        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(len([m for m in self.messages() if m["role"] == "coach"]), 1)

    def test_crash_after_staged_reply_leaves_no_live_duplicate(self):
        runner = FakeRunner(fail_after_reply=True)
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.accept("one", "request-one")

        queue.drain_until_idle(ignore_retry_time=True)

        self.assertEqual(len(runner.calls), 2)
        replies = [m for m in self.messages() if m["role"] == "coach"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["text"], "reply to one")

    def test_prepared_result_is_applied_after_restart_without_rerunning(self):
        runner = FakeRunner()
        queue = PrepareOnlyQueue(self.data, runner, retry_base_seconds=0)
        accepted = queue.accept("prepared", "request-prepared")

        queue.drain_once(ignore_retry_time=True)
        self.assertEqual(queue.snapshot()["jobs"][0]["state"], "prepared")
        self.assertEqual(runner.calls, [accepted["messageId"]])
        self.assertFalse([m for m in self.messages() if m["role"] == "coach"])

        restarted = CoachQueue(self.data, runner, retry_base_seconds=0)
        restarted.drain_until_idle(ignore_retry_time=True)
        replies = [m for m in self.messages() if m["role"] == "coach"]
        self.assertEqual(runner.calls, [accepted["messageId"]])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["replyTo"], accepted["messageId"])

    def test_live_browser_change_is_merged_with_coach_result(self):
        started = threading.Event()
        release = threading.Event()

        def runner(stage, job):
            started.set()
            release.wait(5)
            state_path = Path(stage) / "data/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["tomorrowPreview"] = "coach update"
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat["messages"].append(
                {"role": "coach", "ts": job["acceptedAt"], "text": "merged reply"}
            )
            chat_path.write_text(json.dumps(chat, indent=2) + "\n", encoding="utf-8")

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.start()
        try:
            queue.accept("merge this", "request-merge")
            self.assertTrue(started.wait(2))
            state_path = self.data / "data/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["streak"] = 7
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            release.set()
            self.assertTrue(queue.wait_idle(5))
        finally:
            release.set()
            queue.stop()

        merged = json.loads((self.data / "data/state.json").read_text(encoding="utf-8"))
        self.assertEqual(merged["streak"], 7)
        self.assertEqual(merged["tomorrowPreview"], "coach update")

    def test_same_day_replan_cannot_remove_or_rewrite_completed_blocks(self):
        state_path = self.data / "data/state.json"
        original_done = {
            "id": "bach-done",
            "pieceId": "bach",
            "title": "Bach — completed work",
            "mins": 25,
            "done": True,
            "detail": "What was actually completed.",
        }
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["today"] = {
            "date": "2026-07-29",
            "focus": "Original plan",
            "blocks": [
                original_done,
                {
                    "id": "scriabin-next",
                    "title": "Scriabin — old plan",
                    "mins": 30,
                    "done": False,
                },
            ],
        }
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        def runner(stage, job):
            staged_state_path = Path(stage) / "data/state.json"
            staged = json.loads(staged_state_path.read_text(encoding="utf-8"))
            staged["today"]["focus"] = "Replanned remainder"
            staged["today"]["blocks"] = [
                {
                    "id": "ligeti-next",
                    "title": "Ligeti — new plan",
                    "mins": 20,
                    "done": False,
                },
                {
                    "id": "bach-done",
                    "title": "Coach rewrote completed work",
                    "mins": 5,
                    "done": False,
                },
            ]
            staged_state_path.write_text(
                json.dumps(staged, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat["messages"].append(
                {
                    "role": "coach",
                    "ts": job["acceptedAt"],
                    "text": "The unfinished work is replanned.",
                }
            )
            chat_path.write_text(
                json.dumps(chat, indent=2) + "\n", encoding="utf-8"
            )

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.accept("Change the rest of today's plan", "same-day-replan")
        queue.drain_until_idle(ignore_retry_time=True)

        final = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(final["today"]["blocks"][0], original_done)
        self.assertEqual(
            [block["id"] for block in final["today"]["blocks"]],
            ["bach-done", "ligeti-next"],
        )
        self.assertEqual(final["today"]["focus"], "Replanned remainder")

    def test_new_day_plan_replaces_prior_completed_blocks(self):
        state_path = self.data / "data/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["today"] = {
            "date": "2026-07-29",
            "focus": "Day 1",
            "blocks": [
                {
                    "id": "day-1-done",
                    "title": "Day 1 work",
                    "mins": 20,
                    "done": True,
                }
            ],
        }
        state_path.write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )

        def runner(stage, job):
            staged_state_path = Path(stage) / "data/state.json"
            staged = json.loads(staged_state_path.read_text(encoding="utf-8"))
            staged["today"] = {
                "date": "2026-07-30",
                "focus": "Day 2",
                "blocks": [
                    {
                        "id": "day-2-next",
                        "title": "Day 2 work",
                        "mins": 25,
                        "done": False,
                    }
                ],
            }
            staged_state_path.write_text(
                json.dumps(staged, indent=2) + "\n", encoding="utf-8"
            )
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat["messages"].append(
                {
                    "role": "coach",
                    "ts": job["acceptedAt"],
                    "text": "Day 2 is ready.",
                }
            )
            chat_path.write_text(
                json.dumps(chat, indent=2) + "\n", encoding="utf-8"
            )

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.accept("Build tomorrow", "new-day-plan")
        queue.drain_until_idle(ignore_retry_time=True)

        final = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(final["today"]["date"], "2026-07-30")
        self.assertEqual(
            [block["id"] for block in final["today"]["blocks"]],
            ["day-2-next"],
        )

    def test_block_completed_while_coach_plans_is_preserved(self):
        started = threading.Event()
        release = threading.Event()
        state_path = self.data / "data/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["today"] = {
            "date": "2026-07-29",
            "focus": "Original plan",
            "blocks": [
                {
                    "id": "active-now",
                    "title": "Work in progress",
                    "mins": 20,
                    "done": False,
                    "detail": "Original instructions.",
                }
            ],
        }
        state_path.write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )

        def runner(stage, job):
            staged_state_path = Path(stage) / "data/state.json"
            staged = json.loads(staged_state_path.read_text(encoding="utf-8"))
            staged["today"]["focus"] = "New remainder"
            staged["today"]["blocks"] = [
                {
                    "id": "new-next",
                    "title": "New next block",
                    "mins": 15,
                    "done": False,
                }
            ]
            staged_state_path.write_text(
                json.dumps(staged, indent=2) + "\n", encoding="utf-8"
            )
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat["messages"].append(
                {
                    "role": "coach",
                    "ts": job["acceptedAt"],
                    "text": "The remaining work is changed.",
                }
            )
            chat_path.write_text(
                json.dumps(chat, indent=2) + "\n", encoding="utf-8"
            )
            started.set()
            release.wait(5)

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.start()
        try:
            queue.accept("Change what comes next", "concurrent-completion")
            self.assertTrue(started.wait(2))
            live = json.loads(state_path.read_text(encoding="utf-8"))
            live["today"]["blocks"][0]["done"] = True
            state_path.write_text(
                json.dumps(live, indent=2) + "\n", encoding="utf-8"
            )
            release.set()
            self.assertTrue(queue.wait_idle(5))
        finally:
            release.set()
            queue.stop()

        final = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            final["today"]["blocks"][0],
            {
                "id": "active-now",
                "title": "Work in progress",
                "mins": 20,
                "done": True,
                "detail": "Original instructions.",
            },
        )
        self.assertEqual(
            [block["id"] for block in final["today"]["blocks"]],
            ["active-now", "new-next"],
        )

    def test_confirmed_drop_cannot_resurrect_concurrently_edited_future_block(self):
        runner_started = threading.Event()
        release_runner = threading.Event()
        state_path = self.data / "data/state.json"
        state_path.write_text(
            json.dumps(
                {
                    "startDate": "2026-07-29",
                    "recitalDate": "2026-09-04",
                    "pieces": [
                        {"id": "alpha", "title": "Alpha", "short": "Alpha"},
                        {"id": "beta", "title": "Beta", "short": "Beta"},
                    ],
                    "today": {
                        "focus": "Alpha",
                        "blocks": [
                            {
                                "id": "alpha-next",
                                "title": "Alpha repair",
                                "done": False,
                            },
                            {
                                "id": "beta-next",
                                "title": "Beta repair",
                                "done": False,
                            },
                        ],
                    },
                    "tomorrowPreview": "Alpha and Beta",
                    "flags": [],
                    "week": {
                        "goals": ["Alpha", "Beta"],
                        "gate": ["Alpha", "Beta"],
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        def runner(stage, job):
            staged_state_path = Path(stage) / "data/state.json"
            staged = json.loads(staged_state_path.read_text(encoding="utf-8"))
            staged["pieces"] = [
                piece for piece in staged["pieces"] if piece["id"] != "alpha"
            ]
            staged["today"]["focus"] = "Beta"
            staged["today"]["blocks"] = [
                block
                for block in staged["today"]["blocks"]
                if block["id"] != "alpha-next"
            ]
            staged["tomorrowPreview"] = "Beta"
            staged["week"] = {"goals": ["Beta"], "gate": ["Beta"]}
            staged_state_path.write_text(
                json.dumps(staged, indent=2) + "\n", encoding="utf-8"
            )
            ledger_path = Path(stage) / "data/repertoire-changes.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["changes"].append(
                {
                    "id": "drop-alpha",
                    "action": "drop",
                    "status": "planned",
                    "pieceId": "alpha",
                }
            )
            ledger_path.write_text(
                json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
            )
            chat_path = Path(stage) / "data/chat.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat["messages"].append(
                {
                    "role": "coach",
                    "ts": job["acceptedAt"],
                    "text": "Alpha removed.",
                }
            )
            chat_path.write_text(
                json.dumps(chat, indent=2) + "\n", encoding="utf-8"
            )
            runner_started.set()
            release_runner.wait(5)

        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        queue.start()
        try:
            queue.accept("drop Alpha", "drop-alpha-request")
            self.assertTrue(runner_started.wait(2))
            live = json.loads(state_path.read_text(encoding="utf-8"))
            live["today"]["blocks"][0]["detail"] = "edited on phone"
            state_path.write_text(
                json.dumps(live, indent=2) + "\n", encoding="utf-8"
            )
            release_runner.set()
            self.assertTrue(queue.wait_idle(5))
        finally:
            release_runner.set()
            queue.stop()

        final = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual([piece["id"] for piece in final["pieces"]], ["beta"])
        self.assertEqual(
            [block["id"] for block in final["today"]["blocks"]], ["beta-next"]
        )
        self.assertEqual(
            final["week"], {"goals": ["Beta"], "gate": ["Beta"]}
        )

    def test_queue_first_acceptance_recovers_transient_chat_write_failure(self):
        runner = FakeRunner()
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        original = queue._ensure_user_message
        failed_once = threading.Event()

        def fail_once(job):
            if not failed_once.is_set():
                failed_once.set()
                raise OSError("transient chat write failure")
            return original(job)

        queue._ensure_user_message = fail_once
        queue.start()
        try:
            with self.assertRaises(OSError):
                queue.accept("durable first", "queue-first-request")
            self.assertTrue(queue.wait_idle(5))
        finally:
            queue.stop()

        jobs = queue.snapshot()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "done")
        self.assertEqual(
            [(m["role"], m["text"]) for m in self.messages()],
            [("user", "durable first"), ("coach", "reply to durable first")],
        )

    def test_first_start_migrates_legacy_trailing_users_into_fifo(self):
        chat_path = self.data / "data/chat.json"
        chat_path.write_text(
            json.dumps(
                {
                    "messages": [
                        {
                            "role": "coach",
                            "ts": "2026-07-29T10:00:00Z",
                            "text": "previous reply",
                        },
                        {
                            "role": "user",
                            "ts": "2026-07-29T10:01:00Z",
                            "text": "stranded one",
                        },
                        {
                            "role": "user",
                            "ts": "2026-07-29T10:02:00Z",
                            "text": "stranded two",
                        },
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        runner = FakeRunner()
        queue = CoachQueue(self.data, runner, retry_base_seconds=0)
        migrated = queue.snapshot()["jobs"]
        self.assertEqual([job["sequence"] for job in migrated], [1, 2])
        self.assertTrue(all(job["messageId"].startswith("message-legacy-") for job in migrated))

        queue.drain_until_idle(ignore_retry_time=True)

        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(
            [(m["role"], m["text"]) for m in self.messages()],
            [
                ("coach", "previous reply"),
                ("user", "stranded one"),
                ("coach", "reply to stranded one"),
                ("user", "stranded two"),
                ("coach", "reply to stranded two"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
