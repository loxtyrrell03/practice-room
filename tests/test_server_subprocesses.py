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
            server.HIDDEN_SUBPROCESS_FLAGS,
            run.call_args.kwargs["creationflags"],
        )
        self.assertIs(
            server.HIDDEN_SUBPROCESS_STARTUPINFO,
            run.call_args.kwargs["startupinfo"],
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
            server.HIDDEN_SUBPROCESS_FLAGS,
            run.call_args.kwargs["creationflags"],
        )
        self.assertIs(
            server.HIDDEN_SUBPROCESS_STARTUPINFO,
            run.call_args.kwargs["startupinfo"],
        )
        self.assertEqual(server.os.name == "nt", run.call_args.kwargs["shell"])
        save_session.assert_called_once_with("session-from-windowless-test")

    def test_codex_cli_uses_selected_model_and_reasoning(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(server.shutil, "which", return_value="codex"),
                patch.object(
                    server.subprocess,
                    "run",
                    return_value=Completed(stdout="done"),
                ) as run,
            ):
                server.run_codex(
                    Path(temp),
                    "test prompt",
                    "test",
                    model="gpt-5.6-terra",
                    effort="xhigh",
                )

        command = run.call_args.args[0]
        self.assertIn("gpt-5.6-terra", command)
        self.assertIn('model_reasoning_effort="xhigh"', command)
        self.assertNotIn("--ignore-rules", command)
        self.assertEqual("-", command[-1])
        self.assertIn("Read and obey AGENTS.md", run.call_args.kwargs["input"])
        self.assertIn("delegates to CLAUDE.md", run.call_args.kwargs["input"])
        self.assertEqual(
            server.CODEX_TIMEOUT_SECONDS,
            run.call_args.kwargs["timeout"],
        )
        self.assertEqual(
            server.HIDDEN_SUBPROCESS_FLAGS,
            run.call_args.kwargs["creationflags"],
        )

    def test_claude_failure_reports_fresh_actionable_error(self):
        stale = Completed(
            returncode=1,
            stdout='{"result":"No conversation found with session ID: stale"}',
        )
        blocked = Completed(
            returncode=1,
            stdout='{"result":"Subscription access disabled","api_error_status":403}',
        )
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(server.shutil, "which", return_value="claude"),
                patch.object(server, "load_session", return_value="stale"),
                patch.object(server, "clear_session") as clear_session,
                patch.object(
                    server.subprocess,
                    "run",
                    side_effect=[stale, blocked],
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "Subscription access disabled.*HTTP 403"
                ):
                    server.run_claude(Path(temp), "test prompt", "test")
        clear_session.assert_called_once_with("stale")

    @unittest.skipUnless(
        server.os.name == "nt", "Windows console behavior only"
    )
    def test_windows_process_tree_gets_one_hidden_console(self):
        self.assertEqual(
            subprocess.CREATE_NEW_CONSOLE,
            server.HIDDEN_SUBPROCESS_FLAGS,
        )
        self.assertTrue(
            server.HIDDEN_SUBPROCESS_STARTUPINFO.dwFlags
            & subprocess.STARTF_USESHOWWINDOW
        )
        self.assertEqual(
            subprocess.SW_HIDE,
            server.HIDDEN_SUBPROCESS_STARTUPINFO.wShowWindow,
        )

        grandchild_probe = (
            "import ctypes; "
            "h=ctypes.windll.kernel32.GetConsoleWindow(); "
            "visible=ctypes.windll.user32.IsWindowVisible(h); "
            "print(int(bool(h)), visible)"
        )
        parent_probe = (
            "import subprocess, sys; "
            f"r=subprocess.run([sys.executable, '-c', {grandchild_probe!r}], "
            "capture_output=True, text=True, check=True); "
            "print(r.stdout.strip())"
        )
        result = subprocess.run(
            [server.sys.executable, "-c", parent_probe],
            capture_output=True,
            text=True,
            check=True,
            creationflags=server.HIDDEN_SUBPROCESS_FLAGS,
            startupinfo=server.HIDDEN_SUBPROCESS_STARTUPINFO,
        )
        self.assertEqual("1 0", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
