"""Guard and validate canonical repertoire changes made through coach chat.

The message queue runs the coach against an isolated private-data repository.
This module applies an unambiguous programme change to that isolated snapshot
before the coach plans around it, then rejects the result unless every live
planning surface is consistent.  The queue's prepared-result transaction makes
the accepted change and the coach's answer one atomic, restart-safe unit.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


UK_TZ = ZoneInfo("Europe/London")
LEDGER_REL = "data/repertoire-changes.json"
STATE_REL = "data/state.json"
PLAN_REL = "context/plan.md"
REPERTOIRE_REL = "context/repertoire.md"
MEMORY_REL = "memory/MEMORY.md"
CHAT_REL = "data/chat.json"
JOURNAL_REL = "data/journal.json"
SPOTS_REL = "data/spots.json"

REQUIRED_ADDITION_FIELDS = (
    "title",
    "version",
    "currentState",
    "durationMinutes",
    "deadline",
    "targetTempo",
)
ADDITION_PLAN_FIELDS = (
    "learningTimeline",
    "memorisationTimeline",
    "performanceExposure",
    "physicalRiskConstraints",
    "cutPriority",
    "dailyMinutes",
    "gateTargets",
    "prescriptions",
)

UNCERTAIN_RE = re.compile(
    r"\b(?:maybe|might|possibly|perhaps|considering|thinking\s+about|"
    r"not\s+sure|unsure|wonder(?:ing)?|should\s+i|could\s+i|what\s+if|"
    r"i\s+think\s+i(?:'|’)ll|leaning\s+towards?)\b",
    re.IGNORECASE,
)
DROP_RE = re.compile(
    r"\b(?:i\s*(?:am|'m|’m)\s+)?(?:definitely\s+)?dropping\b|"
    r"\b(?:i(?:'ve|’ve|\s+have)\s+decided\s+to\s+)?(?:drop|remove|cut)\b|"
    r"\btak(?:e|ing)\b.+?\bout\s+of\s+(?:the\s+)?(?:programme|program|recital)\b",
    re.IGNORECASE,
)
ADD_RE = re.compile(
    r"\b(?:i\s*(?:am|'m|’m)\s+)?(?:definitely\s+)?adding\b|"
    r"\badd\b.+?\b(?:to|into)\s+(?:the|my|our)\s+(?:programme|program|recital)\b",
    re.IGNORECASE,
)
FIELD_RE = re.compile(
    r"(?im)^\s*(title|piece|version|edition|arrangement|current\s+state|state|"
    r"duration|deadline|target\s+tempo|tempo)\s*[:=]\s*(.+?)\s*$"
)


class RepertoireChangeError(RuntimeError):
    """Base error for guarded repertoire changes."""


class RepertoireConsistencyError(RepertoireChangeError):
    """The coach result did not consistently apply a confirmed decision."""


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, value) -> None:
    _atomic_write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    )


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def _read_json(path: Path, default):
    text = _read_text(path)
    if not text:
        return copy.deepcopy(default)
    return json.loads(text)


def _normalise(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("♯", " sharp ").replace("♭", " flat ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso_local_date(value: str | None) -> tuple[str, str]:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UK_TZ)
    except ValueError:
        parsed = datetime.now(tz=UK_TZ)
    local = parsed.astimezone(UK_TZ)
    return local.date().isoformat(), local.isoformat(timespec="seconds")


def _day_number(state: dict, effective_date: str) -> int | None:
    try:
        start = datetime.fromisoformat(str(state["startDate"])).date()
        current = datetime.fromisoformat(effective_date).date()
        return (current - start).days + 1
    except (KeyError, TypeError, ValueError):
        return None


def _piece_aliases(piece: dict) -> list[str]:
    aliases = []
    for key in ("id", "short", "title"):
        normalised = _normalise(piece.get(key))
        if len(normalised) >= 3 and normalised not in aliases:
            aliases.append(normalised)
    for alias in piece.get("aliases") or []:
        normalised = _normalise(alias)
        if len(normalised) >= 3 and normalised not in aliases:
            aliases.append(normalised)
    return aliases


def _references_piece(value, piece: dict) -> bool:
    haystack = _normalise(
        json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    )
    if not haystack:
        return False
    for alias in _piece_aliases(piece):
        if alias in haystack:
            return True
    return False


def _matching_pieces(target: str, pieces: list[dict]) -> list[dict]:
    wanted = _normalise(target)
    if len(wanted) < 3:
        return []
    exact = []
    partial = []
    for piece in pieces:
        aliases = _piece_aliases(piece)
        if wanted in aliases:
            exact.append(piece)
        elif any(
            len(alias) >= 4 and (alias in wanted or wanted in alias)
            for alias in aliases
        ):
            partial.append(piece)
    return exact or partial


def _parse_labeled_fields(text: str) -> dict:
    fields = {}
    names = {
        "title": "title",
        "piece": "title",
        "version": "version",
        "edition": "version",
        "arrangement": "version",
        "current state": "currentState",
        "state": "currentState",
        "duration": "durationMinutes",
        "deadline": "deadline",
        "target tempo": "targetTempo",
        "tempo": "targetTempo",
    }
    for match in FIELD_RE.finditer(text):
        key = names[" ".join(match.group(1).lower().split())]
        value = match.group(2).strip()
        if key == "durationMinutes":
            duration = re.search(r"(\d+(?:\.\d+)?)", value)
            if duration and float(duration.group(1)) > 0:
                number = float(duration.group(1))
                fields[key] = int(number) if number.is_integer() else number
        elif value:
            fields[key] = value
    return fields


def _action_target(text: str, match: re.Match, action: str) -> str:
    tail = text[match.end() :].strip(" \t:,-–—")
    tail = re.split(
        r"(?i)\s+(?:from|out\s+of|to|into)\s+(?:the|my|our)\s+"
        r"(?:working\s+)?(?:programme|program|recital)\b",
        tail,
        maxsplit=1,
    )[0]
    tail = re.split(
        r"(?im)\n\s*(?:version|edition|arrangement|current\s+state|state|"
        r"duration|deadline|target\s+tempo|tempo)\s*:",
        tail,
        maxsplit=1,
    )[0]
    tail = tail.split(";")[0].strip(" \t.,!?\"'")
    if action == "drop":
        tail = re.sub(
            r"(?i)^(?:the\s+piece\s+|piece\s+)", "", tail
        ).strip()
    return tail


def _slug(title: str) -> str:
    value = _normalise(title).replace(" ", "-").strip("-")
    return (value[:44].rstrip("-") or "piece")


def _active_ids(state: dict) -> list[str]:
    return [
        str(piece.get("id"))
        for piece in state.get("pieces") or []
        if isinstance(piece, dict) and piece.get("id")
    ]


def _canonical_piece_identities(state: dict) -> list[dict]:
    identity_fields = (
        "id",
        "title",
        "short",
        "version",
        "currentState",
        "durationMinutes",
        "deadline",
        "targetTempo",
    )
    return [
        {key: copy.deepcopy(piece.get(key)) for key in identity_fields}
        for piece in state.get("pieces") or []
        if isinstance(piece, dict)
    ]


def _future_planning(state: dict) -> dict:
    today = state.get("today") or {}
    return {
        "today": {
            "date": copy.deepcopy(today.get("date")),
            "focus": copy.deepcopy(today.get("focus")),
            "blocks": copy.deepcopy(today.get("blocks") or []),
        },
        "tomorrowPreview": copy.deepcopy(state.get("tomorrowPreview")),
        "flags": copy.deepcopy(state.get("flags") or []),
        "week": copy.deepcopy(state.get("week") or {}),
    }


def _history_keys(doc: dict, collection: str) -> set:
    keys = set()
    for item in doc.get(collection) or []:
        if not isinstance(item, dict):
            continue
        if item.get("id"):
            keys.add(("id", str(item["id"])))
        elif item.get("date") is not None:
            keys.add(("date-day", str(item.get("date")), str(item.get("day"))))
    return keys


def _coach_reply(chat: dict) -> str:
    for message in reversed(chat.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "coach":
            return str(message.get("text") or "")
    return ""


def _ledger_default() -> dict:
    return {"version": 1, "pending": [], "changes": []}


class RepertoireChangeManager:
    """Apply, describe, and validate one chat-driven repertoire directive."""

    def __init__(self, data_repo):
        self.root = Path(data_repo)

    def _path(self, rel: str) -> Path:
        return self.root / rel

    def _load_state(self) -> dict:
        state = _read_json(self._path(STATE_REL), {})
        if not isinstance(state.get("pieces"), list):
            raise RepertoireChangeError("state.json pieces must be an array")
        return state

    def _load_ledger(self) -> dict:
        ledger = _read_json(self._path(LEDGER_REL), _ledger_default())
        if not isinstance(ledger, dict):
            raise RepertoireChangeError("repertoire change ledger must be an object")
        ledger.setdefault("version", 1)
        ledger.setdefault("pending", [])
        ledger.setdefault("changes", [])
        if not isinstance(ledger["pending"], list) or not isinstance(
            ledger["changes"], list
        ):
            raise RepertoireChangeError("repertoire change ledger arrays are invalid")
        return ledger

    def classify(self, text: str, state: dict, ledger: dict) -> dict:
        raw = str(text or "").strip()
        labeled = _parse_labeled_fields(raw)
        drop_match = DROP_RE.search(raw)
        add_match = ADD_RE.search(raw)
        uncertain = bool(UNCERTAIN_RE.search(raw))

        if drop_match and add_match:
            return {
                "kind": "clarify",
                "reason": "multiple-changes",
                "question": "Confirm one programme change at a time: which single piece is changing first?",
            }
        if uncertain and (drop_match or add_match):
            action = "drop" if drop_match else "add"
            return {
                "kind": "clarify",
                "reason": f"ambiguous-{action}",
                "question": (
                    f"Are you definitely instructing me to {action} this piece, "
                    "or are you weighing the option?"
                ),
            }

        if drop_match:
            target = labeled.get("title") or _action_target(raw, drop_match, "drop")
            matches = _matching_pieces(target, state["pieces"])
            if len(matches) == 1:
                return {"kind": "drop", "piece": copy.deepcopy(matches[0])}
            if len(matches) > 1:
                return {
                    "kind": "clarify",
                    "reason": "multiple-piece-matches",
                    "target": target,
                    "question": (
                        "Which exact working-repertoire piece do you mean: "
                        + ", ".join(piece.get("title", piece.get("id", "")) for piece in matches)
                        + "?"
                    ),
                }
            prior = self._prior_dropped_piece(target, ledger)
            if prior:
                return {
                    "kind": "noop",
                    "reason": "already-dropped",
                    "piece": prior,
                }
            choices = ", ".join(
                piece.get("title", piece.get("id", "")) for piece in state["pieces"]
            )
            return {
                "kind": "clarify",
                "reason": "unknown-piece",
                "target": target,
                "question": (
                    f"I cannot match “{target or 'that piece'}” to the working "
                    f"repertoire. Which of these do you mean: {choices}?"
                ),
            }

        if add_match:
            fields = labeled
            if "title" not in fields:
                target = _action_target(raw, add_match, "add")
                if target:
                    fields["title"] = target
            return self._classify_addition(fields, state, ledger)

        open_pending = [
            pending
            for pending in ledger["pending"]
            if isinstance(pending, dict)
            and pending.get("action") == "add"
            and pending.get("status", "awaiting-details") == "awaiting-details"
        ]
        if labeled and len(open_pending) == 1:
            fields = copy.deepcopy(open_pending[0].get("fields") or {})
            fields.update(labeled)
            result = self._classify_addition(fields, state, ledger)
            result["pendingId"] = open_pending[0].get("id")
            return result
        return {"kind": "none"}

    def _classify_addition(self, fields: dict, state: dict, ledger: dict) -> dict:
        supplied = {
            key: value
            for key, value in fields.items()
            if key in REQUIRED_ADDITION_FIELDS and value not in (None, "")
        }
        title = supplied.get("title")
        if title:
            existing = _matching_pieces(str(title), state["pieces"])
            if existing:
                requested_version = _normalise(supplied.get("version"))
                exact = next(
                    (
                        piece
                        for piece in existing
                        if not requested_version
                        or not piece.get("version")
                        or _normalise(piece.get("version")) == requested_version
                    ),
                    None,
                )
                if exact:
                    return {
                        "kind": "noop",
                        "reason": "already-active",
                        "piece": copy.deepcopy(exact),
                    }
        missing = [
            field for field in REQUIRED_ADDITION_FIELDS if field not in supplied
        ]
        if missing:
            labels = {
                "title": "exact title",
                "version": "version/edition/arrangement",
                "currentState": "current learning and memory state",
                "durationMinutes": "duration in minutes",
                "deadline": "deadline",
                "targetTempo": "target tempo with BPM and beat unit",
            }
            return {
                "kind": "clarify",
                "reason": "addition-missing-essentials",
                "fields": supplied,
                "missing": missing,
                "question": (
                    "I can add it once you give me: "
                    + "; ".join(labels[field] for field in missing)
                    + "."
                ),
            }
        return {"kind": "add", "fields": supplied}

    def _prior_dropped_piece(self, target: str, ledger: dict) -> dict | None:
        for change in reversed(ledger.get("changes") or []):
            if not isinstance(change, dict) or change.get("action") != "drop":
                continue
            piece = change.get("piece") or {}
            if _matching_pieces(target, [piece]):
                return copy.deepcopy(piece)
        return None

    def _snapshot(self, state: dict, ledger: dict) -> dict:
        return {
            "state": copy.deepcopy(state),
            "ledger": copy.deepcopy(ledger),
            "plan": _read_text(self._path(PLAN_REL)),
            "repertoire": _read_text(self._path(REPERTOIRE_REL)),
            "memory": _read_text(self._path(MEMORY_REL)),
            "journal": _read_json(self._path(JOURNAL_REL), {"entries": []}),
            "spots": _read_json(self._path(SPOTS_REL), {"spots": []}),
        }

    def prepare(self, job: dict) -> dict:
        state = self._load_state()
        ledger = self._load_ledger()
        before = self._snapshot(state, ledger)
        directive = self.classify(job.get("text", ""), state, ledger)
        directive["messageId"] = str(job.get("messageId") or "")
        effective_date, recorded_at = _iso_local_date(
            job.get("acceptedAt") or job.get("ts")
        )
        directive["effectiveDate"] = effective_date
        directive["recordedAt"] = recorded_at
        directive["day"] = _day_number(state, effective_date)
        directive["_before"] = before

        if (
            directive["kind"] == "clarify"
            and directive.get("reason") == "addition-missing-essentials"
        ):
            self._save_pending_addition(ledger, directive)
            _atomic_write_json(self._path(LEDGER_REL), ledger)
        elif directive["kind"] == "drop":
            self._prepare_drop(state, ledger, directive)
        elif directive["kind"] == "add":
            self._prepare_add(state, ledger, directive)

        directive["_preparedState"] = self._load_state()
        directive["_preparedLedger"] = self._load_ledger()
        return directive

    def _save_pending_addition(self, ledger: dict, directive: dict) -> None:
        pending_id = directive.get("pendingId")
        pending = next(
            (
                item
                for item in ledger["pending"]
                if isinstance(item, dict) and item.get("id") == pending_id
            ),
            None,
        )
        if pending is None:
            identity = (
                directive.get("messageId")
                or _hash_text(json.dumps(directive.get("fields") or {}, sort_keys=True))[:16]
            )
            pending = {
                "id": f"pending-add-{identity}",
                "action": "add",
                "status": "awaiting-details",
                "createdAt": directive["recordedAt"],
                "messageIds": [],
            }
            ledger["pending"].append(pending)
        pending["updatedAt"] = directive["recordedAt"]
        pending["fields"] = copy.deepcopy(directive.get("fields") or {})
        pending["missing"] = list(directive.get("missing") or [])
        message_id = directive.get("messageId")
        if message_id and message_id not in pending["messageIds"]:
            pending["messageIds"].append(message_id)
        directive["pendingId"] = pending["id"]

    def _change_id(self, directive: dict, piece_id: str) -> str:
        seed = "|".join(
            (
                directive["kind"],
                piece_id,
                directive["effectiveDate"],
                directive.get("messageId") or "",
            )
        )
        return f"repertoire-{directive['kind']}-{_hash_text(seed)[:16]}"

    def _append_change(
        self, ledger: dict, directive: dict, piece: dict, removed_planning=None
    ) -> dict:
        change_id = self._change_id(directive, str(piece["id"]))
        existing = next(
            (
                item
                for item in ledger["changes"]
                if isinstance(item, dict) and item.get("id") == change_id
            ),
            None,
        )
        if existing:
            directive["changeId"] = change_id
            return existing
        change = {
            "id": change_id,
            "action": directive["kind"],
            "status": "applied-awaiting-plan",
            "effectiveDate": directive["effectiveDate"],
            "day": directive.get("day"),
            "recordedAt": directive["recordedAt"],
            "messageId": directive.get("messageId"),
            "pieceId": piece["id"],
            "title": piece["title"],
            "version": piece.get("version"),
            "piece": copy.deepcopy(piece),
        }
        if removed_planning is not None:
            change["removedPlanning"] = copy.deepcopy(removed_planning)
        ledger["changes"].append(change)
        directive["changeId"] = change_id
        return change

    def _prepare_drop(self, state: dict, ledger: dict, directive: dict) -> None:
        piece = directive["piece"]
        state["pieces"] = [
            row for row in state["pieces"] if row.get("id") != piece.get("id")
        ]
        removed = {"blockIds": [], "weekGoals": [], "weekGate": []}
        today = state.get("today") or {}
        blocks = today.get("blocks") or []
        kept = []
        for block in blocks:
            if (
                isinstance(block, dict)
                and not block.get("done")
                and _references_piece(block, piece)
            ):
                removed["blockIds"].append(block.get("id"))
            else:
                kept.append(block)
        today["blocks"] = kept
        if _references_piece(today.get("focus"), piece):
            today["focus"] = ""
        week = state.get("week") or {}
        for key, removed_key in (("goals", "weekGoals"), ("gate", "weekGate")):
            rows = week.get(key) or []
            removed[removed_key] = [
                row for row in rows if _references_piece(row, piece)
            ]
            week[key] = [
                row for row in rows if not _references_piece(row, piece)
            ]
        if _references_piece(state.get("tomorrowPreview"), piece):
            state["tomorrowPreview"] = ""
        flags = state.get("flags") or []
        state["flags"] = [
            flag for flag in flags if not _references_piece(flag, piece)
        ]
        directive["removedPlanning"] = removed
        self._append_change(ledger, directive, piece, removed)
        _atomic_write_json(self._path(STATE_REL), state)
        _atomic_write_json(self._path(LEDGER_REL), ledger)

    def _reuse_piece_id(self, fields: dict, ledger: dict, state: dict) -> str:
        title = _normalise(fields["title"])
        version = _normalise(fields["version"])
        for change in reversed(ledger["changes"]):
            piece = change.get("piece") if isinstance(change, dict) else None
            if (
                isinstance(piece, dict)
                and _normalise(piece.get("title")) == title
                and _normalise(piece.get("version")) == version
            ):
                return str(piece["id"])
        candidate = _slug(fields["title"])
        used = set(_active_ids(state))
        if candidate not in used:
            return candidate
        return f"{candidate[:32]}-{_hash_text(title + '|' + version)[:8]}"

    def _prepare_add(self, state: dict, ledger: dict, directive: dict) -> None:
        fields = copy.deepcopy(directive["fields"])
        piece_id = self._reuse_piece_id(fields, ledger, state)
        piece = {
            "id": piece_id,
            "title": fields["title"],
            "short": fields["title"],
            "version": fields["version"],
            "currentState": fields["currentState"],
            "durationMinutes": fields["durationMinutes"],
            "deadline": fields["deadline"],
            "targetTempo": fields["targetTempo"],
            "security": None,
            "tempoPct": None,
            "lastCold": None,
            "attention": "focus",
            "note": fields["currentState"],
        }
        state["pieces"].append(piece)
        directive["piece"] = copy.deepcopy(piece)
        self._append_change(ledger, directive, piece)
        pending_id = directive.get("pendingId")
        if pending_id:
            ledger["pending"] = [
                item
                for item in ledger["pending"]
                if not (isinstance(item, dict) and item.get("id") == pending_id)
            ]
        _atomic_write_json(self._path(STATE_REL), state)
        _atomic_write_json(self._path(LEDGER_REL), ledger)

    def public_directive(self, directive: dict) -> dict | None:
        if directive.get("kind") == "none":
            return None
        result = {
            key: copy.deepcopy(value)
            for key, value in directive.items()
            if not key.startswith("_") and key not in {"piece"}
        }
        piece = directive.get("piece") or {}
        if piece:
            result["piece"] = {
                key: copy.deepcopy(piece.get(key))
                for key in (
                    "id",
                    "title",
                    "short",
                    "version",
                    "currentState",
                    "durationMinutes",
                    "deadline",
                    "targetTempo",
                )
                if piece.get(key) is not None
            }
        return result

    def validate(self, directive: dict) -> None:
        kind = directive.get("kind")
        if kind == "none":
            return
        state = self._load_state()
        ledger = self._load_ledger()
        before = directive["_before"]
        plan = _read_text(self._path(PLAN_REL))
        repertoire = _read_text(self._path(REPERTOIRE_REL))
        memory = _read_text(self._path(MEMORY_REL))
        chat = _read_json(self._path(CHAT_REL), {"messages": []})
        journal = _read_json(self._path(JOURNAL_REL), {"entries": []})
        spots = _read_json(self._path(SPOTS_REL), {"spots": []})
        reply = _coach_reply(chat)

        if not _history_keys(before["journal"], "entries").issubset(
            _history_keys(journal, "entries")
        ):
            raise RepertoireConsistencyError(
                "a repertoire change must preserve completed journal history"
            )
        if not _history_keys(before["spots"], "spots").issubset(
            _history_keys(spots, "spots")
        ):
            raise RepertoireConsistencyError(
                "a repertoire change must preserve trouble-spot history"
            )

        if kind == "clarify":
            if _canonical_piece_identities(state) != _canonical_piece_identities(
                before["state"]
            ):
                raise RepertoireConsistencyError(
                    "ambiguous or incomplete language must not change the active repertoire"
                )
            if _future_planning(state) != _future_planning(before["state"]):
                raise RepertoireConsistencyError(
                    "clarification must not change future practice planning"
                )
            if plan != before["plan"] or repertoire != before["repertoire"]:
                raise RepertoireConsistencyError(
                    "clarification must not rewrite the repertoire or plan"
                )
            if ledger["changes"] != before["ledger"]["changes"]:
                raise RepertoireConsistencyError(
                    "clarification must not append or alter an applied change"
                )
            if "?" not in reply:
                raise RepertoireConsistencyError(
                    "the coach must ask a clear repertoire clarification question"
                )
            return

        piece = directive.get("piece") or {}
        if kind == "noop":
            if _canonical_piece_identities(state) != _canonical_piece_identities(
                before["state"]
            ):
                raise RepertoireConsistencyError(
                    "an idempotent repeated instruction must not change repertoire"
                )
            if _future_planning(state) != _future_planning(before["state"]):
                raise RepertoireConsistencyError(
                    "an idempotent repeated instruction must not replan"
                )
            if ledger["changes"] != before["ledger"]["changes"]:
                raise RepertoireConsistencyError(
                    "an idempotent repeated instruction must not alter the audit"
                )
            if plan != before["plan"] or repertoire != before["repertoire"]:
                raise RepertoireConsistencyError(
                    "an idempotent repeated instruction must not rewrite the plan"
                )
            if not _references_piece(reply, piece) or not re.search(
                r"\b(?:already|no\s+further\s+change|unchanged)\b",
                reply,
                re.IGNORECASE,
            ):
                raise RepertoireConsistencyError(
                    "the coach must clearly report that the programme is already unchanged"
                )
            return

        matches = [
            item
            for item in ledger["changes"]
            if isinstance(item, dict) and item.get("id") == directive.get("changeId")
        ]
        if len(matches) != 1:
            raise RepertoireConsistencyError(
                "a confirmed repertoire change needs exactly one audit event"
            )
        if directive["effectiveDate"] not in memory or not _references_piece(
            memory, piece
        ):
            raise RepertoireConsistencyError(
                "memory must contain the dated confirmed repertoire decision"
            )
        if plan == before["plan"] or repertoire == before["repertoire"]:
            raise RepertoireConsistencyError(
                "confirmed changes must rebuild both the remaining plan and repertoire brief"
            )
        if not _references_piece(reply, piece):
            raise RepertoireConsistencyError(
                "the coach reply must name the confirmed repertoire change"
            )

        if kind == "drop":
            self._validate_drop(state, plan, repertoire, reply, piece)
        elif kind == "add":
            self._validate_add(state, plan, repertoire, reply, piece)
        else:
            raise RepertoireConsistencyError(f"unknown repertoire action {kind}")

        matches[0]["status"] = "planned"
        matches[0]["validatedAt"] = directive["recordedAt"]
        _atomic_write_json(self._path(LEDGER_REL), ledger)

    def _validate_drop(
        self, state: dict, plan: str, repertoire: str, reply: str, piece: dict
    ) -> None:
        if piece.get("id") in _active_ids(state):
            raise RepertoireConsistencyError(
                "a dropped piece remains in the canonical active repertoire"
            )
        today = state.get("today") or {}
        for block in today.get("blocks") or []:
            if not block.get("done") and _references_piece(block, piece):
                raise RepertoireConsistencyError(
                    "an obsolete unfinished practice block still names the dropped piece"
                )
        future_state = {
            "tomorrowPreview": state.get("tomorrowPreview"),
            "flags": state.get("flags"),
            "week": state.get("week"),
            "focus": today.get("focus"),
        }
        if _references_piece(future_state, piece):
            raise RepertoireConsistencyError(
                "future state planning still names the dropped piece"
            )
        if _references_piece(plan, piece) or _references_piece(repertoire, piece):
            raise RepertoireConsistencyError(
                "the remaining plan or working repertoire still names the dropped piece"
            )
        if not re.search(
            r"\b(?:drop|dropped|remove|removed|cut|taken\s+out)\b",
            reply,
            re.IGNORECASE,
        ):
            raise RepertoireConsistencyError(
                "the coach must clearly report what was removed"
            )

    def _validate_add(
        self, state: dict, plan: str, repertoire: str, reply: str, piece: dict
    ) -> None:
        active = next(
            (
                item
                for item in state.get("pieces") or []
                if item.get("id") == piece.get("id")
            ),
            None,
        )
        if not active:
            raise RepertoireConsistencyError(
                "the added piece is absent from the canonical active repertoire"
            )
        for field in REQUIRED_ADDITION_FIELDS:
            state_value = active.get(field)
            supplied_value = piece.get(field)
            if state_value != supplied_value:
                raise RepertoireConsistencyError(
                    f"the coach changed or omitted supplied addition field {field}"
                )
        planning = active.get("planning")
        if not isinstance(planning, dict):
            raise RepertoireConsistencyError(
                "the added piece lacks its first-class planning record"
            )
        for field in ADDITION_PLAN_FIELDS:
            value = planning.get(field)
            if value in (None, "", []):
                raise RepertoireConsistencyError(
                    f"the added piece planning record lacks {field}"
                )
        if not isinstance(planning.get("dailyMinutes"), (int, float)) or planning[
            "dailyMinutes"
        ] <= 0:
            raise RepertoireConsistencyError(
                "the added piece needs a positive daily workload"
            )
        if not isinstance(planning.get("gateTargets"), list) or not isinstance(
            planning.get("prescriptions"), list
        ):
            raise RepertoireConsistencyError(
                "gate targets and prescriptions must be explicit lists"
            )
        today = state.get("today") or {}
        if not any(
            not block.get("done") and _references_piece(block, active)
            for block in today.get("blocks") or []
            if isinstance(block, dict)
        ):
            raise RepertoireConsistencyError(
                "the added piece needs an appropriate live practice block"
            )
        week = state.get("week") or {}
        if not _references_piece(week.get("goals"), active) or not _references_piece(
            week.get("gate"), active
        ):
            raise RepertoireConsistencyError(
                "the added piece must be integrated into current goals and gates"
            )
        if not _references_piece(plan, active) or not _references_piece(
            repertoire, active
        ):
            raise RepertoireConsistencyError(
                "the plan and working repertoire must both include the added piece"
            )
        if _normalise(active.get("version")) not in _normalise(repertoire):
            raise RepertoireConsistencyError(
                "the working repertoire must retain the supplied version"
            )
        if not re.search(
            r"\b(?:add|added|integrat|replan|replanned)\w*\b",
            reply,
            re.IGNORECASE,
        ):
            raise RepertoireConsistencyError(
                "the coach must clearly report the addition and replanning"
            )


def render_repertoire_prompt(directive: dict | None) -> str:
    """Render the local-server guard as an authoritative coach instruction."""
    if not directive:
        return ""
    payload = json.dumps(directive, ensure_ascii=False, sort_keys=True)
    common = """

## Authoritative repertoire-change guard

The server classified this target message before you ran:
```json
%s
```
The server owns `data/repertoire-changes.json`; do not edit it. Never read,
write, rename, or delete any Desktop PDF. Work only in this staged repository.
""" % payload
    kind = directive.get("kind")
    if kind == "clarify":
        return common + """
Ask the exact missing/clarifying question in one concise reply. Do not add or
remove a piece, rewrite the remaining plan/repertoire brief, or silently assume
any missing musical fact. Ask only for the fields in `missing` when present.
"""
    if kind == "noop":
        return common + """
Make no programme or planning mutation. State clearly that the instruction has
already been applied (or that the piece is already active), so no duplicate
change was made.
"""
    if kind == "drop":
        return common + """
The server has already removed the confirmed piece and obsolete unfinished
blocks in this isolated transaction. Recompute the entire remaining plan from
today: blocks and breaks, current week goals/gates, workload, memorisation and
learning timeline, performance exposure, physical-risk constraints, cut order,
and research-backed prescriptions. Remove the piece from all future treatment
in `context/plan.md` and the active table in `context/repertoire.md`. Preserve
completed journal entries, completed blocks, and trouble-spot history. Add the
dated decision to memory and report exactly what was removed and redistributed.
"""
    if kind == "add":
        return common + """
The server has already inserted the confirmed piece and the pianist's exact
essential facts in this isolated transaction. Do not alter or fill in those
facts. Make it a first-class item: add a `planning` object on the piece with
learningTimeline, memorisationTimeline, performanceExposure,
physicalRiskConstraints, cutPriority, positive dailyMinutes, gateTargets[],
and prescriptions[]. Recompute the entire remaining plan, daily blocks and
breaks, current goals/gates, total workload, performance exposure, physical
risk, cut order and research-backed work. Update both `context/plan.md` and
`context/repertoire.md`, add the dated decision to memory, and report what was
added and what the replan displaced. Mark any non-essential unknown explicitly
and schedule a test or ask one focused follow-up instead of inventing it.
"""
    return common
