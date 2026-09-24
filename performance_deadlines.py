"""Shared performance scope and bounded scheduling pressure. No model required."""
from __future__ import annotations

import copy
import hashlib
import json


def priority(deadline):
    # Missing importance is ordinary priority, not evidence of a major event.
    return deadline.get("priority", "medium")


def applies(deadline, piece, movement):
    if deadline.get("archived"):
        return False
    members = deadline.get("pieceIds")
    if members is not None:
        if piece["id"] not in members:
            return False
    elif deadline["id"] not in movement.get("deadlineIds", piece.get("deadlineIds", [])):
        return False
    scope = deadline.get("movementIdsByPiece", {}).get(piece["id"])
    return scope is None or movement.get("id") in scope


def pressure(days, importance):
    """Smoothly increase attention; even a low-priority event cannot take over.

    Take the strongest applicable event, never sum repeated performances.
    Existing workload weights still determine how much material needs learning.
    """
    # Importance scales the approaching deadline, rather than adding a
    # permanent advantage that can swamp a substantially earlier assessment.
    boost = {"low": 1., "medium": 2.5, "high": 3.5}[importance]
    return 1 + boost * (90 / (max(0, days) + 90)) ** 2


def update_performance(academic, change):
    """Patch one event without overwriting other devices' events or settings."""
    if not isinstance(change, dict):
        raise ValueError("Invalid performance.")
    event_id, request_id = change.get("id"), change.get("requestId")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 120:
        raise ValueError("A performance needs an identity.")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 120:
        raise ValueError("A performance update needs a retry identity.")
    signature = hashlib.sha256(json.dumps(change, sort_keys=True).encode()).hexdigest()
    rows = academic.setdefault("deadlines", [])
    old = next((d for d in rows if d["id"] == event_id), None)
    if old and old.get("lastRequestId") == request_id:
        if old.get("lastRequestSignature") != signature:
            raise ValueError("That retry identity was already used for different changes.")
        return
    revision = old.get("revision", 0) if old else 0
    if type(change.get("revision")) is not int or change["revision"] != revision:
        raise ValueError("This performance changed on another device. Close and reopen it before saving.")
    action = change.get("action", "save")
    if action not in {"save", "archive", "restore"}:
        raise ValueError("Unknown performance action.")
    item = copy.deepcopy(old) if old else {"id": event_id}
    if action == "save":
        if item.get("archived"):
            raise ValueError("This performance was removed. Restore it before editing.")
        for field in ("label", "date", "month", "priority", "pieceIds", "movementIdsByPiece"):
            if field not in change:
                raise ValueError(f"Performance is missing {field}.")
            item[field] = copy.deepcopy(change[field])
        if item["date"] is not None:
            if not isinstance(item["date"], str):
                raise ValueError("Use a valid performance date.")
            item["month"] = item["date"][:7]
        item["status"] = "confirmed" if item["date"] else "provisional"
    else:
        if not old:
            raise ValueError("Unknown performance.")
        item["archived"] = action == "archive"
    item.update(revision=revision + 1, lastRequestId=request_id, lastRequestSignature=signature)
    if old:
        rows[rows.index(old)] = item
    else:
        rows.append(item)
