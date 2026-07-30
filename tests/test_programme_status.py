import unittest

from programme_status import ProgrammeStatusError, validate_programme_statuses


def state_with_points():
    return {
        "pieces": [
            {
                "id": "bach",
                "note": "Prelude behind; fugue map improving.",
                "statusPoints": [
                    {
                        "lead": "Now",
                        "text": "Two fugue pages are relearned, but not yet smooth.",
                    },
                    {
                        "lead": "Main work",
                        "text": "Name harmony before each closed-score landmark start.",
                    },
                    {
                        "lead": "Next checkpoint",
                        "text": "Cover the prelude sectionally by Day 5.",
                    },
                ],
            }
        ]
    }


class ProgrammeStatusTests(unittest.TestCase):
    def test_accepts_short_structured_status(self):
        validate_programme_statuses(state_with_points())

    def test_once_structured_every_piece_needs_points(self):
        state = state_with_points()
        state["pieces"].append({"id": "scriabin", "note": "A paragraph."})
        with self.assertRaisesRegex(ProgrammeStatusError, "2–4 status bullets"):
            validate_programme_statuses(state)

    def test_rejects_long_status_paragraph(self):
        state = state_with_points()
        state["pieces"][0]["statusPoints"][0]["text"] = "x" * 181
        with self.assertRaisesRegex(ProgrammeStatusError, "one concise fact"):
            validate_programme_statuses(state)


if __name__ == "__main__":
    unittest.main()
