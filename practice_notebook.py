"""Personal notes, editable follow-ups and structured preparation stages.

One atomic document accepts a note and its deterministic suggestions together.
No booking or model call is needed, and dismissals survive every schedule refresh.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from practice_logs import atomic_write_json

NOTEBOOK = "data/notebook.json"
UK = ZoneInfo("Europe/London")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(default)


def text(value, label, maximum=500, required=True):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise ValueError(f"{label} must be {'non-empty ' if required else ''}text, up to {maximum} characters.")
    return value.strip()


def calendar_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use a date in YYYY-MM-DD format.")
    return value


def month_at(start, offset):
    n = start.year * 12 + start.month - 1 + offset
    return date(n // 12, n % 12 + 1, 1)


def preparation_routes(state, academic, overrides=None):
    """Proposed targets, not assertions of mastery. No personal IDs in code."""
    routes = []
    start = date.fromisoformat(academic.get("startDate") or date.today().isoformat())
    for piece in state.get("pieces", []):
        if piece.get("active") is False or piece.get("status") == "retired":
            continue
        due = [d for d in academic.get("deadlines", []) if piece["id"] in d.get("pieceIds", [])]
        for deadline in due:
            target = date.fromisoformat(deadline.get("date") or deadline["month"] + "-01")
            # Unknown exact dates keep a whole-month performance window.
            finish = target if deadline.get("date") else month_at(target, 1) - timedelta(days=1)
            months = max(0, (target.year-start.year)*12 + target.month-start.month)
            first = max(1, round(months * .5))
            second = max(first+1, months-1)
            bounds = [start, max(start, month_at(start, min(first, months))),
                      max(start, month_at(start, min(second, months))), max(start, target), max(start, finish)]
            scope = deadline.get("movementIdsByPiece", {}).get(piece["id"])
            movements = [m for m in piece.get("movements", []) if scope is None or m["id"] in scope]
            plan = piece.get("planning") or {}
            is_study = plan.get("offBench", False)
            descriptions = [
                ("Build" if is_study else "Learn", "Learn sections", "Map the sections, settle fingering and connect short phrases.", "Try each section without immediately repeating it first; record the gaps."),
                ("Connect", "Connect the work", "Join sections and work on transitions. Revisit the passages that notes still flag.", "Complete a joined attempt; name any stopping points and the next repair."),
                ("Rehearse", "Rehearse complete attempts", "Practise complete attempts, recovery and starting from different landmarks.", "Record or play to a listener, then note what held up and what needs another visit."),
                ("Due", "Performance window", "Use the confirmed date when known; keep preparation focused on the remaining issues.", "Record actual performance feedback before changing the longer-term plan."),
            ]
            stages = []
            for i, (label, title_, focus, checkpoint) in enumerate(descriptions):
                sid = f"{piece['id']}:{deadline['id']}:{i}"
                end = bounds[i+1] - timedelta(days=1) if i < 3 else bounds[4]
                stage = {"id": sid, "label": label, "title": title_, "startDate": bounds[i].isoformat(),
                         "endDate": max(bounds[i], end).isoformat(), "focus": focus,
                         "checkpoint": checkpoint, "kind": "due" if i == 3 else "work",
                         "movementIds": [m["id"] for m in movements],
                         "targets": [{"title": m["title"], "focus": (m.get("planning") or {}).get("focus", ""),
                                      "checkpoint": (m.get("planning") or {}).get("passTest", "")} for m in movements]}
                if i == 0:
                    stage["focus"] = plan.get("focus") or focus
                    stage["checkpoint"] = plan.get("passTest") or checkpoint
                stage.update((overrides or {}).get(sid, {}))
                stages.append(stage)
            routes.append({"id": f"{piece['id']}:{deadline['id']}", "pieceId": piece["id"],
                           "title": piece.get("short") or piece["title"], "deadlineId": deadline["id"],
                           "deadlineDate": deadline.get("date"), "deadlineMonth": deadline["month"],
                           "scope": ", ".join(m["title"].split(". ")[0] for m in movements) if scope else "",
                           "stages": stages})
    return routes


def suggestions(note):
    """Small, explicit musical heuristics; the original wording stays visible."""
    source = note["text"]
    bars = note.get("bars") or ""
    if not bars:
        match = re.search(r"\b(?:bars?|mm?\.?)\s*(\d+(?:\s*[-–—]\s*\d+)?)", source, re.I)
        bars = match.group(1) if match else ""
    bars = re.sub(r"\s*[-–—]\s*", "–", bars)
    section = re.search(r"\b(exposition|development|recapitulation|coda|ending|transition|introduction)\b", source, re.I)
    location = f"bars {bars}" if bars else ("the " + section.group(1).lower() if section else "the noted passage")
    hand = "LH" if re.search(r"\bLH\b|left hand", source, re.I) else "RH" if re.search(r"\bRH\b|right hand", source, re.I) else ""
    rules = [
        ("fingering", r"fingering.*(?:undecided|unsure|unclear|change|problem)|(?:decide|settle|choose).*fingering", "Settle fingering", "Compare two options slowly, choose one and mark it. Revisit it after another task.", "playing"),
        ("memory", r"(?:memory|memor[yi]|recall).*(?:shaky|weak|uncertain|blank|problem|slip)|(?:forgot|forget|can't remember)", "Check memory landmarks", "Name the harmony and starting cues, recall one short phrase, then check the score. Isolate a hand if that reveals the gap.", "study"),
        ("evenness", r"uneven|rushing|rhythm.*(?:weak|shaky|unstable)|not even", "Even out the passage", "Use a comfortable tempo to locate the uneven join. Try a short rhythmic variation, then return to the written rhythm and listen.", "playing"),
        ("hand", r"(?:LH|RH|left hand|right hand).*(?:weak|shaky|unclear|uneven)|(?:weak|shaky).*(?:LH|RH|hand)", "Clarify the hand part", "Isolate the named hand for a short phrase, settle coordination and fingering, then reconnect both hands.", "playing"),
        ("tempo", r"not.*(?:speed|tempo)|too slow|tempo.*(?:stuck|unstable)|can't.*speed", "Build a reliable tempo", "Find a tempo that stays even and comfortable. Increase only after a clean return; record the tempo that held up.", "playing"),
    ]
    # A resolved observation is evidence to keep, not an automatic new problem.
    if re.search(r"\b(?:no longer|fixed|resolved|now secure|now even)\b|\bnot (?:shaky|uneven|weak)\b", source, re.I):
        return []
    found = []
    for category, pattern, title_, instruction, kind in rules:
        if re.search(pattern, source, re.I):
            found.append({"category": category, "scope": f"{location}:{hand}", "title": f"{title_}{' (' + hand + ')' if hand else ''} · {location}", "instruction": instruction,
                          "bars": bars, "kind": kind, "minutes": 10})
    if not found and re.search(r"\?|\b(?:need|problem|difficult|weak|shaky|unsure|unclear|work on|practise|practice|review)\b", source, re.I):
        found = [{"category": "review:" + hashlib.sha256(source.lower().encode()).hexdigest()[:16],
                  "title": f"Review {location}", "instruction": source, "bars": bars, "kind": "playing", "minutes": 10}]
    return found[:3]


class PracticeNotebook:
    def __init__(self, root, lock, now_fn=None):
        self.root, self.lock = Path(root), lock
        self.now_fn = now_fn or (lambda: datetime.now(UK))

    def load(self):
        doc = read_json(self.root / NOTEBOOK, {"version": 1, "notes": [], "tasks": [], "stages": {}, "revision": 0})
        if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("notes"), list) or not isinstance(doc.get("tasks"), list) or not isinstance(doc.get("stages"), dict):
            raise ValueError("The saved notebook needs attention. Existing notes have been kept.")
        return doc

    def save(self, doc):
        doc["revision"] = doc.get("revision", 0) + 1
        atomic_write_json(self.root / NOTEBOOK, doc)

    def context(self):
        return read_json(self.root / "data/state.json", {"pieces": []}), read_json(self.root / "data/academic-year.json", {})

    def get(self):
        with self.lock:
            doc = self.load()
            state, year = self.context()
            doc["routes"] = preparation_routes(state, year, doc["stages"])
            # Existing block notes remain readable beside free-practice notes.
            active = {p["id"] for p in state.get("pieces", [])}
            old = read_json(self.root / "data/observations.json", {"obs": []}).get("obs", [])
            doc["history"] = [n for n in old if n.get("pieceId") in active and (n.get("localDate") or n.get("ts", "")[:10]) >= year.get("startDate", "")]
            return doc

    def note(self, payload):
        with self.lock:
            doc = self.load()
            state, year = self.context()
            piece = next((p for p in state.get("pieces", []) if p["id"] == payload.get("pieceId") and p.get("active") is not False and p.get("status") != "retired"), None)
            if not piece:
                raise ValueError("Choose a piece from your current repertoire.")
            movement_id = payload.get("movementId") or None
            movement = next((m for m in piece.get("movements", []) if m["id"] == movement_id), None)
            if movement_id and not movement:
                raise ValueError("Choose a movement from that piece.")
            values = {"clientId": text(payload.get("clientId"), "Note identity", 128),
                      "pieceId": piece["id"], "movementId": movement_id,
                      "text": text(payload.get("text"), "Note", 2000),
                      "bars": text(payload.get("bars", ""), "Bars", 80, False)}
            for old in doc["notes"]:
                if old["clientId"] == values["clientId"]:
                    if any(old.get(k) != v for k, v in values.items()):
                        raise ValueError("This note identity already saved different text. Reload before retrying.")
                    return {"note": old, "created": False, "notebook": self.get()}
            now = self.now_fn()
            note = {**values, "id": "note-"+uuid.uuid4().hex, "createdAt": now.isoformat(),
                    "date": now.date().isoformat(), "movement": movement["title"] if movement else None}
            doc["notes"].append(note)
            for idea in suggestions(note):
                key = "|".join([piece["id"], movement_id or "", idea["category"], idea["bars"].lower(), idea.get("scope", "")])
                existing = next((t for t in doc["tasks"] if t["key"] == key), None)
                if existing:
                    existing.setdefault("noteIds", []).append(note["id"])
                    existing["lastSeen"] = now.isoformat()
                    # User edits, dismissals and resolutions are authoritative.
                    continue
                doc["tasks"].append({**idea, "id": "task-"+uuid.uuid4().hex, "key": key,
                                     "pieceId": piece["id"], "movementId": movement_id,
                                     "noteIds": [note["id"]], "status": "open", "revision": 1,
                                     "createdAt": now.isoformat(), "lastSeen": now.isoformat(),
                                     "notBefore": now.date().isoformat()})
            self.save(doc)
            return {"note": note, "created": True, "notebook": self.get()}

    def task(self, payload):
        with self.lock:
            doc = self.load()
            task = next((t for t in doc["tasks"] if t["id"] == payload.get("id")), None)
            if not task:
                raise ValueError("This practice task is no longer available.")
            if payload.get("revision") != task.get("revision"):
                raise ValueError("This task changed on another device. Refresh and try again.")
            update = {}
            for field, maximum in [("title", 250), ("instruction", 2000)]:
                if field in payload:
                    update[field] = text(payload[field], field.capitalize(), maximum)
            if "minutes" in payload:
                if type(payload["minutes"]) is not int or not 5 <= payload["minutes"] <= 45:
                    raise ValueError("Choose between 5 and 45 whole minutes.")
                update["minutes"] = payload["minutes"]
            if "notBefore" in payload:
                update["notBefore"] = calendar_date(payload["notBefore"])
            if "status" in payload:
                if payload["status"] not in {"open", "dismissed", "resolved"}:
                    raise ValueError("Unknown task status.")
                update["status"] = payload["status"]
            if "kind" in payload:
                if payload["kind"] not in {"playing", "study"}:
                    raise ValueError("Choose playing or score study.")
                update["kind"] = payload["kind"]
            task.update(update, revision=task["revision"]+1, editedAt=self.now_fn().isoformat())
            self.save(doc)
            return self.get()

    def stage(self, payload):
        with self.lock:
            doc = self.load()
            if payload.get("revision") != doc.get("revision"):
                raise ValueError("The plan changed on another device. Refresh before saving.")
            state, year = self.context()
            stage = next((s for r in preparation_routes(state, year, doc["stages"]) for s in r["stages"] if s["id"] == payload.get("id")), None)
            if not stage:
                raise ValueError("That preparation stage is no longer in the plan.")
            update = {k: text(payload.get(k), k.capitalize(), 2000 if k != "title" else 150) for k in ("title", "focus", "checkpoint")}
            update.update({k: calendar_date(payload.get(k)) for k in ("startDate", "endDate")})
            if update["endDate"] < update["startDate"]:
                raise ValueError("The end must be on or after the start.")
            update["edited"] = True
            doc["stages"][stage["id"]] = update
            self.save(doc)
            return self.get()
