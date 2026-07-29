import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class WindowlessSubprocessTests(unittest.TestCase):
    def test_git_uses_windowless_creation_flags(self):
        with patch.object(
            server.subprocess,
            "run",
            return_value=Completed(stdout="ok"),
        ) as run:
            success, output = server.git(Path("repo"), "status")

        self.assertTrue(success)
        self.assertEqual("ok", output)
        self.assertEqual(
            server.WINDOWLESS_SUBPROCESS_FLAGS,
            run.call_args.kwargs["creationflags"],
        )

    def test_coach_cli_uses_windowless_creation_flags(self):
        completed = Completed(
            stdout='{"session_id":"session-from-windowless-test"}'
        )
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(server.shutil, "which", return_value="claude"),
                patch.object(server, "load_session", return_value=None),
                patch.object(server, "save_session") as save_session,
                patch.object(
                    server.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                server.run_claude(Path(temp), "test prompt", "test")

        self.assertEqual(
            server.WINDOWLESS_SUBPROCESS_FLAGS,
            run.call_args.kwargs["creationflags"],
        )
        self.assertEqual(server.os.name == "nt", run.call_args.kwargs["shell"])
        save_session.assert_called_once_with("session-from-windowless-test")

    @unittest.skipUnless(
        server.os.name == "nt", "Windows console behavior only"
    )
    def test_windows_flag_is_create_no_window(self):
        self.assertEqual(
            subprocess.CREATE_NO_WINDOW,
            server.WINDOWLESS_SUBPROCESS_FLAGS,
        )


if __name__ == "__main__":
    unittest.main()
