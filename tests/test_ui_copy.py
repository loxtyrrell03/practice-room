import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiCopyTests(unittest.TestCase):
    def test_coach_picker_and_response_metadata_are_visible_contracts(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "app.css").read_text(encoding="utf-8")
        self.assertIn("Claude Opus 5", html)
        self.assertIn("model-trigger-effort", html)
        self.assertIn("response-model", script)
        self.assertIn(".response-model", styles)

    def test_plan_ui_does_not_explain_its_storage_contract(self):
        source = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in ("index.html", "app.js")
        )
        for phrase in (
            "One date, one plan",
            "Dated plans",
            "Ready means timed blocks",
            "separate dated plan",
            "cannot overwrite the active day",
            "logs accounted",
            "provisional",
            "Rough phase snapshot",
            "prepared future work",
            "previous day's logs",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, source)


if __name__ == "__main__":
    unittest.main()
