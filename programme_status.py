"""Validation for concise, structured Programme descriptions."""
from __future__ import annotations


class ProgrammeStatusError(RuntimeError):
    """A coach-produced Programme description is unreadable or incomplete."""


def validate_programme_statuses(state: dict) -> None:
    pieces = state.get("pieces") or []
    if not isinstance(pieces, list):
        raise ProgrammeStatusError("state.pieces must be an array")

    # Legacy/test snapshots without structured points remain readable. Once any
    # piece uses the format, every active piece must use it so one cannot regress
    # to paragraph prose during a later coach turn.
    structured = any(
        isinstance(piece, dict) and "statusPoints" in piece for piece in pieces
    )
    if not structured:
        return

    for piece in pieces:
        if not isinstance(piece, dict) or not piece.get("id"):
            raise ProgrammeStatusError("every programme piece needs an id")
        label = f"programme piece {piece['id']}"
        points = piece.get("statusPoints")
        if not isinstance(points, list) or not 2 <= len(points) <= 4:
            raise ProgrammeStatusError(f"{label} needs 2–4 status bullets")
        leads = set()
        for index, point in enumerate(points, start=1):
            if not isinstance(point, dict):
                raise ProgrammeStatusError(
                    f"{label} status bullet {index} must be an object"
                )
            lead = str(point.get("lead") or "").strip()
            text = str(point.get("text") or "").strip()
            if not 2 <= len(lead) <= 40:
                raise ProgrammeStatusError(
                    f"{label} status bullet {index} needs a short bold lead"
                )
            if not 5 <= len(text) <= 180:
                raise ProgrammeStatusError(
                    f"{label} status bullet {index} needs one concise fact"
                )
            normalised = lead.casefold()
            if normalised in leads:
                raise ProgrammeStatusError(f"{label} repeats the lead {lead!r}")
            leads.add(normalised)
        note = str(piece.get("note") or "").strip()
        if len(note) > 180:
            raise ProgrammeStatusError(
                f"{label} legacy note must be one short fallback sentence"
            )
