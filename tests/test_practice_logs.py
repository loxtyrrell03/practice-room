import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from practice_logs import ObservationPipeline


class SimulatedCrash(BaseException):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class FakeCoach:
    """A deliberately non-idempotent effect generator.

    If the pipeline ever asks it to run the same routed observation twice, the
    spots/history and memory assertions below expose the duplicate.
    """

    def __init__(self, fail_times=0, crash_times=0, delay=0):
        self.fail_times = fail_times
        self.crash_times = crash_times
        self.delay = delay
        self.calls = []
        self.started = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, stage, batch):
        with self.lock:
            self.calls.append(
                {
                    "id": batch["id"],
                    "source": batch["source"],
                    "route": list(batch.get("routeObservationIds", [])),
                    "review": list(batch.get("reviewObservationIds", [])),
                }
            )
            call_number = len(self.calls)
        self.started.set()
        if self.delay:
            time.sleep(self.delay)
        if call_number <= self.crash_times:
            raise SimulatedCrash("runner process died")
        if call_number <= self.crash_times + self.fail_times:
            raise RuntimeError("fake Claude failure")

        observations = read_json(Path(stage) / "data/observations.json")["obs"]
        by_id = {row["id"]: row for row in observations}
        spots_path = Path(stage) / "data/spots.json"
        spots = read_json(spots_path)
        memory_path = Path(stage) / "memory/MEMORY.md"
        memory = memory_path.read_text(encoding="utf-8")
        for observation_id in batch.get("routeObservationIds", []):
            row = by_id[observation_id]
            spots["spots"].append(
                {
                    "id": f"spot-{observation_id}",
                    "piece": "piece-1",
                    "bars": "1",
                    "issue": row["text"],
                    "history": [
                        {
                            "date": row["localDate"],
                            "note": "reported",
                            "observationId": observation_id,
                        }
                    ],
                }
            )
            memory += f"- routed {observation_id}\n"
        write_json(spots_path, spots)
        memory_path.write_text(memory, encoding="utf-8")

        if batch["source"] == "coach":
            chat_path = Path(stage) / "data/chat.json"
            chat = read_json(chat_path)
            chat["messages"].append(
                {"role": "coach", "ts": "2026-07-29T19:00:00Z", "text": "landed"}
            )
            write_json(chat_path, chat)


class PracticeLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data-repo"
        write_json(self.data / "data/observations.json", {"obs": []})
        write_json(self.data / "data/spots.json", {"spots": []})
        write_json(
            self.data / "data/state.json",
            {
                "startDate": "2026-07-29",
                "recitalDate": "2026-09-04",
                "streak": 0,
                "pieces": [
                    {
                        "id": "piece-1",
                        "security": 10,
                        "tempoPct": 50,
                        "attention": "focus",
                        "note": "fixture",
                    }
                ],
                "today": {"date": "2026-07-29", "focus": "fixture", "blocks": []},
                "tomorrowPreview": "",
                "flags": [],
                "week": {},
            },
        )
        write_json(
            self.data / "data/chat.json",
            {
                "messages": [
                    {
                        "role": "user",
                        "ts": "2026-07-29T18:00:00Z",
                        "text": "Debrief: fixture",
                    }
                ]
            },
        )
        write_json(self.data / "data/journal.json", {"entries": []})
        memory = self.data / "memory/MEMORY.md"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("# Fixture memory\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def pipeline(self, **kwargs):
        return ObservationPipeline(self.data, **kwargs)

    def submit(self, pipeline, client_id, when, text=None):
        return pipeline.submit(
            {
                "clientId": client_id,
                "day": 1,
                "blockId": "block-1",
                "block": "Fixture block",
                "text": text or f"note {client_id}",
            },
            now=when,
        )

    def observation_rows(self):
        return read_json(self.data / "data/observations.json")["obs"]

    def batches(self):
        return read_json(self.data / "data/observation-jobs.json")["batches"]

    def effects(self):
        spots = read_json(self.data / "data/spots.json")["spots"]
        memory = (self.data / "memory/MEMORY.md").read_text(encoding="utf-8")
        return spots, memory

    def test_submit_is_immediate_and_client_retry_is_idempotent(self):
        pipeline = self.pipeline()
        when = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
        first, created_first = self.submit(pipeline, "phone-request-1", when)
        second, created_second = self.submit(pipeline, "phone-request-1", when)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first["id"], second["id"])
        rows = self.observation_rows()
        self.assertEqual(1, len(rows))
        self.assertEqual("pending", rows[0]["status"])
        self.assertEqual("2026-07-29", rows[0]["localDate"])

    def test_submit_preserves_movement_identity(self):
        pipeline = self.pipeline()
        entry, created = pipeline.submit(
            {
                "clientId": "scriabin-presto-note",
                "day": 1,
                "blockId": "scriabin-presto-map",
                "block": "Scriabin — II. Presto: map",
                "pieceId": "scriabin",
                "movementId": "2-presto",
                "movement": "II. Presto",
                "text": "RH weak b.87",
            },
            now=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(created)
        self.assertEqual("scriabin", entry["pieceId"])
        self.assertEqual("2-presto", entry["movementId"])
        self.assertEqual("II. Presto", entry["movement"])

    def test_submit_rejects_partial_movement_identity(self):
        pipeline = self.pipeline()
        with self.assertRaisesRegex(
            ValueError, "movementId and movement must be supplied together"
        ):
            pipeline.submit(
                {
                    "clientId": "partial-movement",
                    "day": 1,
                    "blockId": "scriabin-presto-map",
                    "block": "Scriabin — II. Presto: map",
                    "pieceId": "scriabin",
                    "movementId": "2-presto",
                    "text": "RH weak b.87",
                },
                now=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
            )

    def test_daily_batches_all_eligible_logs_without_chat_or_debrief(self):
        pipeline = self.pipeline()
        runner = FakeCoach()
        self.submit(
            pipeline,
            "one",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        self.submit(
            pipeline,
            "two",
            datetime(2026, 7, 29, 19, 0, tzinfo=timezone.utc),
        )
        chat_before = (self.data / "data/chat.json").read_text(encoding="utf-8")
        journal_before = (self.data / "data/journal.json").read_text(encoding="utf-8")

        result = pipeline.run_due(
            runner,
            now=datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc),
        )
        again = pipeline.run_due(
            runner,
            now=datetime(2026, 7, 29, 22, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("processed", result["status"])
        self.assertEqual(2, result["count"])
        self.assertEqual("skipped", again["status"])
        self.assertEqual(1, len(runner.calls))
        self.assertEqual("daily", runner.calls[0]["source"])
        self.assertEqual({"processed"}, {row["status"] for row in self.observation_rows()})
        self.assertEqual(
            chat_before, (self.data / "data/chat.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            journal_before,
            (self.data / "data/journal.json").read_text(encoding="utf-8"),
        )

    def test_log_after_cutoff_waits_for_next_daily_batch(self):
        pipeline = self.pipeline()
        runner = FakeCoach()
        before, _ = self.submit(
            pipeline,
            "before",
            datetime(2026, 7, 29, 19, 29, tzinfo=timezone.utc),
        )
        after, _ = self.submit(
            pipeline,
            "after",
            datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc),
        )

        first = pipeline.run_due(
            runner,
            now=datetime(2026, 7, 29, 22, 0, tzinfo=timezone.utc),
        )
        statuses = {row["id"]: row["status"] for row in self.observation_rows()}
        self.assertEqual(1, first["count"])
        self.assertEqual("processed", statuses[before["id"]])
        self.assertEqual("pending", statuses[after["id"]])

        second = pipeline.run_due(
            runner,
            now=datetime(2026, 7, 30, 19, 31, tzinfo=timezone.utc),
        )
        self.assertEqual(1, second["count"])
        self.assertEqual(2, len(runner.calls))
        self.assertEqual(
            "processed",
            {row["id"]: row["status"] for row in self.observation_rows()}[after["id"]],
        )

    def test_early_debrief_processes_and_daily_batch_skips(self):
        pipeline = self.pipeline()
        runner = FakeCoach()
        self.submit(
            pipeline,
            "debrief-note",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )

        debrief = pipeline.process_for_coach(
            runner,
            source_key="message-1",
            is_debrief=True,
            now=datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc),
        )
        daily = pipeline.run_due(
            runner,
            now=datetime(2026, 7, 29, 20, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("processed", debrief["status"])
        self.assertEqual("processed", daily["status"])
        self.assertFalse(daily["runnerCalled"])
        self.assertEqual(1, len(runner.calls))
        row = self.observation_rows()[0]
        self.assertEqual("processed", row["status"])
        self.assertIsNotNone(row["acknowledgedAt"])
        self.assertEqual(1, len(self.effects()[0]))

    def test_daily_processed_note_is_reviewed_not_rerouted_at_debrief(self):
        pipeline = self.pipeline()
        runner = FakeCoach()
        entry, _ = self.submit(
            pipeline,
            "review-later",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        pipeline.run_due(
            runner,
            now=datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc),
        )
        self.assertIsNone(self.observation_rows()[0]["acknowledgedAt"])

        result = pipeline.process_for_coach(
            runner,
            source_key="message-debrief",
            is_debrief=True,
            now=datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("processed", result["status"])
        self.assertEqual([], runner.calls[1]["route"])
        self.assertEqual([entry["id"]], runner.calls[1]["review"])
        self.assertIsNotNone(self.observation_rows()[0]["acknowledgedAt"])
        self.assertEqual(1, len(self.effects()[0]))

    def test_missed_schedule_catches_up_after_restart(self):
        first_process = self.pipeline()
        self.submit(
            first_process,
            "sleep-note",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        runner = FakeCoach()
        restarted = self.pipeline()

        result = restarted.run_due(
            runner,
            now=datetime(2026, 7, 30, 7, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("daily-2026-07-29", result["batchId"])
        self.assertEqual("processed", result["status"])
        self.assertEqual(1, len(runner.calls))
        self.assertEqual("processed", self.observation_rows()[0]["status"])

    def test_failure_is_auditable_and_retry_has_one_effect(self):
        pipeline = self.pipeline()
        runner = FakeCoach(fail_times=1)
        self.submit(
            pipeline,
            "retry-note",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        due = datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc)

        failed = pipeline.run_due(runner, now=due)
        failed_row = self.observation_rows()[0]
        self.assertEqual("failed", failed["status"])
        self.assertEqual("failed", failed_row["status"])
        self.assertEqual(1, failed_row["attempts"])
        self.assertIn("fake Claude failure", failed_row["lastError"])
        self.assertEqual([], self.effects()[0])

        retried = pipeline.run_due(
            runner,
            now=due + timedelta(minutes=6),
            ignore_backoff=True,
        )
        self.assertEqual("processed", retried["status"])
        self.assertEqual(2, len(runner.calls))
        self.assertEqual(2, self.observation_rows()[0]["attempts"])
        self.assertEqual(1, len(self.effects()[0]))

    def test_failed_daily_batch_keeps_its_identity_across_next_day_restart(self):
        pipeline = self.pipeline()
        failing = FakeCoach(fail_times=1)
        self.submit(
            pipeline,
            "cross-day-retry",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        first = pipeline.run_due(
            failing,
            now=datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc),
        )
        self.assertEqual("daily-2026-07-29", first["batchId"])

        restarted = self.pipeline()
        runner = FakeCoach()
        retry = restarted.run_due(
            runner,
            now=datetime(2026, 7, 30, 19, 31, tzinfo=timezone.utc),
            ignore_backoff=True,
        )

        self.assertEqual("daily-2026-07-29", retry["batchId"])
        self.assertEqual("processed", retry["status"])
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(1, len(self.effects()[0]))

    def test_restart_recovers_interrupted_runner_then_retries(self):
        pipeline = self.pipeline()
        crashing = FakeCoach(crash_times=1)
        self.submit(
            pipeline,
            "runner-crash",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        due = datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc)
        with self.assertRaises(SimulatedCrash):
            pipeline.run_due(crashing, now=due)
        self.assertEqual("processing", self.observation_rows()[0]["status"])

        restarted = self.pipeline()
        recovered = restarted.recover(now=due + timedelta(minutes=1))
        self.assertEqual([], recovered["recovered"])
        self.assertEqual("failed", self.observation_rows()[0]["status"])
        runner = FakeCoach()
        result = restarted.run_due(
            runner,
            now=due + timedelta(minutes=2),
            ignore_backoff=True,
        )
        self.assertEqual("processed", result["status"])
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(1, len(self.effects()[0]))

    def test_prepared_transaction_recovers_without_duplicate_effect(self):
        crashed = {"done": False}

        def die_after_apply(_batch_id):
            if not crashed["done"]:
                crashed["done"] = True
                raise SimulatedCrash("power loss after effects")

        pipeline = self.pipeline(after_apply_hook=die_after_apply)
        runner = FakeCoach()
        self.submit(
            pipeline,
            "apply-crash",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        due = datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc)
        with self.assertRaises(SimulatedCrash):
            pipeline.run_due(runner, now=due)

        self.assertEqual("prepared", self.batches()["daily-2026-07-29"]["status"])
        self.assertEqual("processing", self.observation_rows()[0]["status"])
        self.assertEqual(1, len(self.effects()[0]))

        restarted = self.pipeline()
        recovered = restarted.recover(now=due + timedelta(minutes=1))
        second = restarted.run_due(
            runner,
            now=due + timedelta(minutes=2),
        )
        self.assertEqual(["daily-2026-07-29"], recovered["recovered"])
        self.assertEqual("skipped", second["status"])
        self.assertEqual(1, len(runner.calls))
        spots, memory = self.effects()
        self.assertEqual(1, len(spots))
        self.assertEqual(1, memory.count("- routed "))
        self.assertEqual("processed", self.observation_rows()[0]["status"])

    def test_prepared_conflict_is_visible_and_never_reruns_coach(self):
        def die_after_apply(_batch_id):
            raise SimulatedCrash("power loss after effects")

        pipeline = self.pipeline(after_apply_hook=die_after_apply)
        runner = FakeCoach()
        self.submit(
            pipeline,
            "prepared-conflict",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        due = datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc)
        with self.assertRaises(SimulatedCrash):
            pipeline.run_due(runner, now=due)

        spots_path = self.data / "data/spots.json"
        spots = read_json(spots_path)
        spots["spots"].append({"id": "external-change"})
        write_json(spots_path, spots)

        restarted = self.pipeline()
        restarted.recover(now=due + timedelta(minutes=1))
        result = restarted.run_due(runner, now=due + timedelta(minutes=2))

        self.assertEqual("recovering", result["status"])
        self.assertEqual(1, len(runner.calls))
        self.assertEqual("failed", self.observation_rows()[0]["status"])
        self.assertIn("conflicts", self.observation_rows()[0]["lastError"])
        self.assertEqual("prepared", self.batches()["daily-2026-07-29"]["status"])

    def test_concurrent_phone_and_laptop_submissions_do_not_lose_rows(self):
        pipeline = self.pipeline()
        when = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

        def add(index):
            return self.submit(pipeline, f"request-{index}", when)[0]["id"]

        with ThreadPoolExecutor(max_workers=12) as executor:
            ids = list(executor.map(add, range(60)))

        rows = self.observation_rows()
        self.assertEqual(60, len(rows))
        self.assertEqual(60, len(set(ids)))
        self.assertEqual(60, len({row["clientId"] for row in rows}))

    def test_concurrent_scheduler_ticks_run_one_batch(self):
        pipeline = self.pipeline()
        runner = FakeCoach(delay=0.15)
        self.submit(
            pipeline,
            "concurrent-due",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        due = datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc)
        first_result = {}

        def first():
            first_result.update(pipeline.run_due(runner, now=due))

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(runner.started.wait(1))
        second = pipeline.run_due(runner, now=due)
        thread.join()

        self.assertEqual("processed", first_result["status"])
        self.assertEqual("busy", second["status"])
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(1, len(self.effects()[0]))

    def test_sync_failure_retries_backup_without_rerunning_coach(self):
        sync_results = iter([False, True])
        sync_calls = []

        def sync(_reason):
            sync_calls.append(True)
            return next(sync_results)

        pipeline = self.pipeline(sync_callback=sync)
        runner = FakeCoach()
        self.submit(
            pipeline,
            "sync-failure",
            datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        )
        result = pipeline.run_due(
            runner,
            now=datetime(2026, 7, 29, 19, 31, tzinfo=timezone.utc),
        )
        batch = self.batches()["daily-2026-07-29"]
        self.assertEqual("processed", result["status"])
        self.assertEqual("failed", batch["syncStatus"])

        self.assertTrue(pipeline.retry_sync())
        batch = self.batches()["daily-2026-07-29"]
        self.assertEqual("synced", batch["syncStatus"])
        self.assertEqual(2, len(sync_calls))
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(1, len(self.effects()[0]))

    def test_default_schedule_is_2030_europe_london(self):
        pipeline = self.pipeline()
        summary = pipeline.summary(
            now=datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)
        )
        self.assertEqual("20:30", summary["dailyTime"])
        self.assertEqual("Europe/London", summary["timezone"])
        # 20:30 BST is 19:30 UTC.
        self.assertEqual("2026-07-29T19:30:00Z", summary["nextDueAt"])


if __name__ == "__main__":
    unittest.main()
