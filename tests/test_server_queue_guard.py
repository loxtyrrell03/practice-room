import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class QueueBackupGuardTests(unittest.TestCase):
    def test_startup_backup_reads_durable_queue_before_manager_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            (data / ".coach-queue.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "nextSequence": 2,
                        "jobs": [{"id": "job-1", "state": "queued"}],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(server, "DATA", data),
                patch.object(server, "coach_queue", None),
                patch.object(server, "git") as fake_git,
            ):
                self.assertFalse(server.sync_push("startup recovery"))
                fake_git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
