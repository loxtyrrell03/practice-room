"""Deterministic, booking-bounded practice allocation for an academic year.

The coach owns repertoire/evidence/prescriptions; this module owns fitting that
work into confirmed reservations. No reservation is created or changed here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from performance_deadlines import applies, pressure, priority

UK = ZoneInfo("Europe/London")
VERSION = 1


def instant(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=UK) if result.tzinfo is None else result.astimezone(UK)


def booking_time(row, end=False):
    return instant(f"{row['date']}T{row['endTime' if end else 'startTime']}:00")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _key(row):
    return str(row.get("id") or f"asimut:{row['eventId']}")


def _weight(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return 1.0
    return number if math.isfinite(number) and number > 0 else 1.0


def _candidates(state, now, academic=None):
    items = []
    deadlines = {d['id']: d for d in (academic or {}).get('deadlines', [])}
    for piece in state.get("pieces", []):
        if piece.get('active') is False or piece.get('status') == 'retired':
            continue
        plan = piece.get("planning") or {}
        base = _weight(plan.get("weight"))
        # Unknown readiness never means zero mastery. Explicit priority, not a
        # fabricated score, determines allocation until evidence is available.
        movements = piece.get("movements") or [{"id": None, "title": ""}]
        total_weight = sum(_weight((m.get('planning') or {}).get('weight')) for m in movements)
        for movement in movements:
            mp = movement.get("planning") or {}
            weight = base * _weight(mp.get("weight")) / total_weight
            windows = []
            for deadline in deadlines.values():
                if not applies(deadline, piece, movement):
                    continue
                if deadline.get('date') and deadline.get('status') == 'confirmed':
                    target = instant(deadline['date']+'T00:00:00')
                    expiry = target+timedelta(days=1)
                elif deadline.get('month'):
                    target = instant(deadline['month']+'-01T00:00:00')
                    expiry = (target.replace(day=28)+timedelta(days=4)).replace(day=1)
                else:
                    continue
                windows.append((target, expiry, priority(deadline)))
            if not windows and "deadlines" not in (academic or {}):
                month = mp.get("deadlineMonth") or plan.get("deadlineMonth")
                if month:
                    target = instant(month+'-01T00:00:00')
                    windows = [(target, (target.replace(day=28)+timedelta(days=4)).replace(day=1), "high")]
            future = [(target, importance) for target, expiry, importance in windows if expiry > now]
            if future:
                weight *= max(pressure((target.date()-now.date()).days, importance)
                              for target, importance in future)
            elif windows:
                # A passed assessment does not imply completion. Keep a single
                # maintenance identity until the pianist confirms retirement.
                weight *= .45
            items.append({"key": f"{piece['id']}:{movement.get('id') or ''}",
                          "piece": piece, "movement": movement,
                          "weight": weight,
                          "kind": "study" if plan.get("offBench") or piece.get("id") == "lecture-recital" else "playing",
                          "focus": mp.get("focus") or plan.get("focus"),
                          "passTest": mp.get("passTest") or plan.get("passTest")})
    return items


def _steps(item, mins, spot=None):
    focus = item.get("focus") or "Choose one short unfinished phrase; decide fingering and hear its musical direction before linking it to its neighbour."
    if spot:
        focus = f"{spot.get('bars', 'Named passage')}: {spot.get('issue', 'Review the reported difficulty')}. {spot.get('prescription') or focus}"
    if item["kind"] == "study":
        return [{"lead": f"{mins} min · Score and lecture study", "text": focus[:240]},
                {"lead": "Stop when", "text": (item.get("passTest") or "Write one decision, its evidence, and the next question to test at the piano.")[:240]}]
    return [
        {"lead": f"{mins} min · Diagnose and repair", "text": focus[:240]},
        {"lead": "1 pass · Retrieve, then check", "text": "After a comfortable warm-up, try one familiar start without first repeating it. Check against the score; name the exact failure before repairing."},
        {"lead": "2 passes · Link and listen", "text": "Choose a tempo that preserves coordination and intended sound. Work the named phrase hands together; isolate a hand only for a specific obstacle, then reconnect."},
        {"lead": "Pass when", "text": (item.get("passTest") or "Return after another task and play the target phrase accurately, with the intended sound and easy movement. Log the result and tempo; retest next session.")[:240]},
    ]


def _block(booking, index, kind, title, mins, cursor, item=None, spot=None):
    block = {"id": f"{_key(booking)}:{index}", "bookingId": _key(booking),
             "title": title, "mins": mins, "kind": kind, "done": False,
             "status": "planned", "flag": None,
             "start": cursor.isoformat(), "end": (cursor + timedelta(minutes=mins)).isoformat()}
    if item:
        block.update(pieceId=item["piece"]["id"], movementId=item["movement"].get("id"),
                     movement=item["movement"].get("title") or None,
                     steps=_steps(item, mins, spot),
                     why="Balanced across booked time, performance importance and dates, movement coverage and recent practice. The duration is a planning choice, not a proven learning optimum.")
    else:
        messages = {"break": "Leave the keyboard; relax your hands, move and rest. Resume only if comfortable.",
                    "setup": "Settle into the room and warm up gently. Stop playing if pain or altered sensation appears.",
                    "close": "Record what changed, the passage and tempo. Name the next test; leave the room on time."}
        block.update(steps=[{"lead": f"{mins} min · {title}", "text": messages.get(kind, title)},
                            {"lead": "Stop when", "text": "The allotted time ends; protect the next booking and your comfort."}], why="Included inside the booked room time.")
    return block


WORK = {"playing", "study"}
TOUCHED = {"active", "paused", "done", "skipped"}


def _fresh(source, now):
    try:
        return -60 <= (now-instant(source["observedAt"])).total_seconds() <= 1800
    except (KeyError, ValueError, TypeError):
        return False


def _touched(block):
    return bool(block.get("done") or block.get("startedAt") or block.get("status") in TOUCHED)


def _minutes(block):
    return block.get("actualMinutes", block["mins"]) if block.get("done") else block["mins"]


def _ceil_minute(moment):
    clean = moment.replace(second=0, microsecond=0)
    return clean+timedelta(minutes=1) if clean < moment else clean


def _subtract(intervals, cuts):
    for cut_start, cut_end in sorted(cuts):
        updated = []
        for start, end in intervals:
            if cut_end <= start or cut_start >= end:
                updated.append((start, end))
            else:
                if start < cut_start:
                    updated.append((start, cut_start))
                if cut_end < end:
                    updated.append((cut_end, end))
        intervals = updated
    return intervals


def _union_minutes(intervals):
    total, last_start, last_end = 0, None, None
    for start, end in sorted(intervals):
        if last_end is None:
            last_start, last_end = start, end
        elif start <= last_end:
            last_end = max(last_end, end)
        else:
            total += (last_end-last_start).total_seconds()/60
            last_start, last_end = start, end
    return int(total+((last_end-last_start).total_seconds()/60 if last_end else 0))


def _summary(session):
    for key, kinds in (("playingMinutes", {"playing"}), ("studyMinutes", {"study"}),
                       ("restMinutes", {"break", "setup", "close"})):
        session[key] = sum(_minutes(b) for b in session.get("blocks", []) if b["kind"] in kinds and b.get("status") not in {"skipped", "missed"})
    return session


def _make_block(booking, kind, title, mins, cursor, item=None, spot=None):
    identity = fingerprint([cursor.isoformat(), kind, item["key"] if item else "", mins])[:12]
    return _block(booking, identity, kind, title, mins, cursor, item, spot)


def reconcile(snapshot, state, academic, previous=None, spots=None, *, now=None, force=False, tasks=None, routes=None):
    """Rebuild unfinished remainder inside exact usable reservation segments."""
    now = (now or datetime.now(UK)).astimezone(UK)
    previous = copy.deepcopy(previous or {"version": VERSION, "sessions": []})
    covered = set(snapshot.get("completeCoveredDates") or [])
    rows = snapshot.get("reservations") or []
    complete = (snapshot.get("status") == "ready" or snapshot.get("snapshotStatus") == "complete") and bool(covered)
    fresh = snapshot.get("status") == "ready" or snapshot.get("isFresh", snapshot.get("sourceFreshAtImport", False))
    if not complete or not fresh or not _fresh(snapshot, now):
        previous["sync"] = {"status": "stale" if previous.get("sessions") else "unavailable",
                            "message": "Waiting for a fresh complete ASIMUT agenda. Previous plans are retained; availability is not confirmed.",
                            "observedAt": snapshot.get("observedAt"), "coveredDates": sorted(covered)}
        return previous
    candidates = _candidates(state, now, academic)
    if not candidates:
        return {**previous, "sync": {"status": "unavailable", "message": "Add repertoire before planning sessions."}}
    signature = fingerprint({"bookings": rows, "conflicts": snapshot.get("conflicts"), "covered": sorted(covered),
                             "pieces": state.get("pieces"), "coachBlocks": state.get("blocks"), "academic": academic,
                             "spots": spots, "tasks": tasks, "routes": routes, "minute": now.strftime("%Y-%m-%dT%H:%M"), "lastAction": previous.get("lastActionAt")})
    if signature == previous.get("inputSignature") and not force:
        previous["sync"] = {"status": "current", "observedAt": snapshot.get("observedAt"), "coveredDates": sorted(covered)}
        return previous
    old = {s["id"]: s for s in previous.get("sessions", [])}
    by_key = {_key(b): b for b in rows}
    allocations = {c["key"]: 0.0 for c in candidates}
    last_done, recent, daily, sessions, protected = {}, {}, {}, [], []
    task_visits = set()
    for sid, prior in old.items():
        outside = prior["date"] not in covered and sid not in by_key
        past = (booking_time(by_key[sid], True) if sid in by_key else instant(prior["end"])) <= now
        preserved = [b for b in prior.get("blocks", []) if _touched(b) or instant(b["end"]) <= now or outside]
        for block in preserved:
            if block.get("taskId") and _touched(block):
                task_visits.add((block["taskId"], prior["date"]))
            if not _touched(block) and instant(block["end"]) <= now:
                block["status"] = "missed"
            if block["kind"] in WORK and (block.get("done") or block.get("status") in {"active", "paused"}):
                daily[prior["date"]] = daily.get(prior["date"], 0)+_minutes(block)
                key = f"{block.get('pieceId')}:{block.get('movementId') or ''}"
                if key in allocations and block.get("done"):
                    completed = instant(block.get("completedAt", block["end"]))
                    if now-timedelta(days=7) <= completed <= now:
                        allocations[key] += _minutes(block)
                    last_done[key] = max(last_done.get(key, completed), completed)
            if block.get("status") in {"active", "paused"}:
                protected.append((max(now, instant(block["start"])), max(now, instant(block["end"]))))
            elif block.get("done") and instant(block.get("completedAt", block["end"])) > now:
                protected.append((max(now, instant(block["start"])), instant(block.get("completedAt", block["end"]))))
        prior["blocks"] = preserved
        if outside or past or sid not in by_key:
            if outside and not past:
                prior.update(bookingStatus="unverified", notice="Outside the latest complete agenda. Refresh before starting.")
            elif sid not in by_key and not past:
                prior.update(bookingStatus="cancelled", notice="No longer in the complete agenda. Completed and started work is preserved.")
            sessions.append(_summary(prior))
    cuts = [(booking_time(c), booking_time(c, True)) for c in snapshot.get("conflicts", [])]
    claimed, last_item = [], None
    limits = academic.get("sessionLimits") or {}
    default_cap = int((academic.get("dailyTargetMinutes") or {}).get("max", 360))
    for booking in sorted(rows, key=lambda b: (b["date"], b["startTime"], str(b["eventId"]))):
        sid = _key(booking)
        start, end = booking_time(booking), booking_time(booking, True)
        if end <= now or booking["date"] not in covered:
            continue
        # Evaluate urgency for the day being planned, including passed events.
        candidates = _candidates(state, max(now, start), academic)
        for item in candidates:
            last = last_done.get(item["key"])
            if last:
                item["weight"] *= 1+min(.5, max(0, (now-last).days-2)*.1)
            matches = [b for b in state.get("blocks", []) if b.get("pieceId") == item["piece"]["id"] and
                       b.get("movementId") == item["movement"].get("id") and b.get("steps")]
            if matches:
                item["coachSteps"] = matches[-1]["steps"]
        weight_total = sum(item['weight'] for item in candidates)
        for item in candidates:
            item['allocationWeight'] = item['weight']/weight_total
        prior = old.get(sid) or {}
        blocks = prior.get("blocks", [])
        max_active = max(0, int((academic.get("dailyOverrides") or {}).get(booking["date"], {}).get("maxMinutes", default_cap)))
        limit = max(0, int(limits[sid])) if sid in limits else int((end-start).total_seconds()//60)
        usable_end = min(end, start+timedelta(minutes=limit))
        start_work = max(start, _ceil_minute(now))
        allowed = _subtract([(max(start, now), usable_end)] if usable_end > max(start, now) else [], cuts+claimed+protected)
        free = [(max(a, start_work), b) for a,b in allowed if b > max(a, start_work)]
        claimed.extend(allowed)
        session = {"id": sid, "eventId": booking["eventId"], "date": booking["date"],
                   "start": start.isoformat(), "end": end.isoformat(), "room": booking["room"],
                   "sourceUrl": booking.get("sourceUrl"), "bookedMinutes": int((end-start).total_seconds()//60),
                   "bookingStatus": "confirmed", "blocks": blocks,
                   "allowedSegments": [{"start": a.isoformat(), "end": b.isoformat()} for a,b in allowed],
                   "usableSegments": [{"start": a.isoformat(), "end": b.isoformat()} for a,b in free],
                   "availableMinutes": sum(int((b-a).total_seconds()//60) for a,b in free)}
        if sid in limits:
            session["sessionLimitMinutes"] = limit
        started_conflict = any(b.get("status") in {"active", "paused"} and
            (b.get('startedRoom', prior.get('room')) != booking['room'] or instant(b["start"]) < start or instant(b["end"]) > usable_end or
             _subtract([(instant(b["start"]), instant(b["end"]))], cuts) != [(instant(b["start"]), instant(b["end"]))]) for b in blocks)
        if started_conflict:
            session.update(notice="Booking or available time changed around a started block. Pause and check the room and time.", needsAttention=True)
        sessions.append(session)
        daily.setdefault(booking["date"], 0)
        for segment_start, segment_end in free:
            duration = int((segment_end-segment_start).total_seconds()//60)
            if duration < 10 or daily[booking["date"]] >= max_active:
                continue
            cursor = segment_start
            already_warm = any(_touched(b) and 0 <= (segment_start-instant(b.get("completedAt", b["end"]))).total_seconds() <= 600 for b in blocks)
            setup = 0 if already_warm else min(5, duration//4)
            if setup:
                blocks.append(_make_block(booking, "setup", "Settle in and warm up", setup, cursor))
                cursor += timedelta(minutes=setup)
            remaining, continuous = duration-setup-3, 0
            if not setup:
                preceding = sorted([b for b in blocks if _touched(b) and instant(b['end']) <= cursor], key=lambda b:b['end'], reverse=True)
                edge = cursor
                for earlier in preceding:
                    if earlier['kind'] != 'playing' or (edge-instant(earlier['end'])).total_seconds() >= 300:
                        break
                    continuous += _minutes(earlier)
                    edge = instant(earlier['start'])
            while remaining >= 5 and daily[booking["date"]] < max_active:
                if continuous >= 40:
                    if remaining < 10:
                        break
                    blocks.append(_make_block(booking, "break", "Break", 5, cursor))
                    cursor += timedelta(minutes=5); remaining -= 5; continuous = 0
                # Rotation is a bounded preference: forced alternation would
                # silently erase priority differences with just two works.
                item = min(candidates, key=lambda c: (allocations[c["key"]]/c['allocationWeight']+
                    (12 if recent.get(c["key"]) == booking["date"] else 0)+
                    (12 if c['key'] == last_item else 0), c["key"]))
                followups = [t for t in (tasks or []) if t.get("status") == "open" and
                    t.get("pieceId") == item["piece"]["id"] and
                    (not t.get("movementId") or t["movementId"] == item["movement"].get("id")) and
                    t.get("notBefore", "") <= booking["date"] and (t["id"], booking["date"]) not in task_visits]
                task = min(followups, key=lambda t: (t.get("lastSeen", ""), t["id"])) if followups else None
                stage_options = [s for r in (routes or []) if r["pieceId"] == item["piece"]["id"]
                    for s in r["stages"] if s.get("edited") and s["startDate"] <= booking["date"] <= s["endDate"]
                    and (not s.get("movementIds") or item["movement"].get("id") in s["movementIds"])]
                if stage_options:
                    stage = min(stage_options, key=lambda s: (s["endDate"], s["id"]))
                    item = {**item, "focus": stage["focus"], "passTest": stage["checkpoint"], "coachSteps": []}
                if task:
                    item = {**item, "kind": task.get("kind", "playing")}
                mins = min(task["minutes"] if task else (25 if item["kind"] == "playing" else 15), remaining, max_active-daily[booking["date"]])
                if not task and remaining-mins < 5 and remaining <= 30:
                    mins = min(remaining, max_active-daily[booking["date"]])
                if mins < 5:
                    break
                p, m = item["piece"], item["movement"]
                title = p.get("short") or p["title"]
                if m.get("title"):
                    title += " · "+m["title"]
                matching = [s for s in (spots or {}).get("spots", []) if s.get("piece") == p["id"] and
                    s.get("movementId") == m.get("id") and s.get("status") in {"open", "watching"} and
                    str(s.get("lastSeen") or s.get("logged") or "")[:10] >= (now-timedelta(days=10)).date().isoformat()]
                block = _make_block(booking, item["kind"], title, mins, cursor, item, matching[-1] if matching else None)
                if task:
                    block.update(taskId=task["id"], title=title + " · " + task["title"],
                                 steps=[{"lead": "Focus", "text": task["instruction"]},
                                        {"lead": "Check", "text": "Return to the passage after another task and jot what changed."}])
                    task_visits.add((task["id"], booking["date"]))
                for step in ([] if task else item.get("coachSteps", [])[:2]):
                    if isinstance(step, dict) and step.get("text"):
                        block["steps"].insert(1, {"lead": "Coach focus", "text": str(step["text"])[:240]})
                blocks.append(block)
                cursor += timedelta(minutes=mins); remaining -= mins
                continuous = continuous+mins if item["kind"] == "playing" else 0
                allocations[item["key"]] += mins; daily[booking["date"]] += mins
                recent[item["key"]] = booking["date"]; last_item = item["key"]
            if cursor < segment_end:
                close = min(3, int((segment_end-cursor).total_seconds()//60))
                if close:
                    blocks.append(_make_block(booking, "close", "Log and leave", close, cursor))
        session["blocks"] = sorted(blocks, key=lambda b: (b["start"], b["id"]))
        if not free or not any(b.get("status") == "planned" for b in blocks):
            session.setdefault("notice", "No additional work fits: remaining time, conflicts, your limit or daily cap.")
        _summary(session)
    return {"version": VERSION, "inputSignature": signature, "generatedAt": now.isoformat(),
            "lastActionAt": previous.get("lastActionAt"),
            "reason": "Rebalanced to confirmed bookings, recent practice and current repertoire priorities.",
            "sync": {"status": "current", "observedAt": snapshot.get("observedAt"), "coveredDates": sorted(covered)},
            "sessions": sorted(sessions, key=lambda s: (s["start"], s["id"]))}


def overview(document, academic, *, now=None):
    now = (now or datetime.now(UK)).astimezone(UK)
    result = copy.deepcopy(document)
    sync = result.get("sync", {})
    if sync.get("status") == "current" and not _fresh(sync, now):
        sync.update(status="stale", message="Bookings need a fresh ASIMUT check; retained plans are unverified.")
    days, intervals = {}, {}
    def day_entry(day):
        return days.setdefault(day, {"date": day, "bookedMinutes": 0, "playingMinutes": 0, "studyMinutes": 0,
                                     "restMinutes": 0, "doneMinutes": 0})
    for day in sync.get("coveredDates", []):
        day_entry(day)
    for session in result.get("sessions", []):
        session["timeStatus"] = "past" if instant(session["end"]) <= now else "in-progress" if instant(session["start"]) <= now else "upcoming"
        day = day_entry(session["date"])
        confirmed = session.get("bookingStatus") == "confirmed"
        if confirmed:
            intervals.setdefault(session["date"], []).append((instant(session["start"]), instant(session["end"])))
        for block in session.get("blocks", []):
            kind = block["kind"]
            allowed_now = any(instant(a['start']) <= now < instant(a['end']) for a in session.get('allowedSegments', []))
            if block.get('status') == 'paused' and instant(block['start']) <= now < instant(block['end']):
                allowed_now = True
            block["canStart"] = (confirmed and sync.get("status") == "current" and not session.get("needsAttention") and
                instant(session["start"]) <= now < instant(session["end"]) and block.get("status") in {"planned", "paused"} and
                allowed_now)
            if block.get("done") and kind in WORK:
                day["doneMinutes"] += _minutes(block)
            if confirmed and block.get("status") not in {"skipped", "missed"}:
                day["playingMinutes" if kind == "playing" else "studyMinutes" if kind == "study" else "restMinutes"] += _minutes(block)
    target = academic.get("dailyTargetMinutes") or {"min":240,"max":360}
    for day in days.values():
        day["bookedMinutes"] = _union_minutes(intervals.get(day["date"], []))
        day["focusedMinutes"] = day["playingMinutes"]+day["studyMinutes"]
        day["targetGapMinutes"] = max(0, target["min"]-day["focusedMinutes"])
        day["availabilityConfirmed"] = sync.get("status") == "current" and day["date"] in sync.get("coveredDates", [])
    result["days"] = sorted(days.values(), key=lambda d:d["date"])
    result["dailyTargetMinutes"], result["today"] = target, now.date().isoformat()
    return result


def apply_action(document, payload, *, now=None):
    now = (now or datetime.now(UK)).astimezone(UK)
    result = copy.deepcopy(document)
    session = next((s for s in result.get("sessions", []) if s["id"] == payload.get("sessionId")), None)
    if not session:
        raise ValueError("Session no longer exists. Refresh your schedule.")
    block = next((b for b in session["blocks"] if b["id"] == payload.get("blockId")), None)
    if not block:
        raise ValueError("Block changed. Refresh your schedule.")
    action, status = payload.get("action"), block.get("status", "planned")
    if action not in {"start", "complete", "skip", "reset", "pause", "resume"}:
        raise ValueError("Unknown practice action")
    if action in {"start", "resume"}:
        if status not in {"planned", "paused"}:
            raise ValueError("Only a planned or paused block can start.")
        if session.get("bookingStatus") != "confirmed" or result.get("sync", {}).get("status") != "current" or not _fresh(result["sync"], now):
            raise ValueError("Refresh and confirm the booking before starting.")
        if session.get("needsAttention"):
            raise ValueError("Check the changed booking before continuing this session.")
        if not (instant(session["start"]) <= now < instant(session["end"])):
            raise ValueError("Practice can start only during the booked room period.")
        allowed = next((a for a in session.get('allowedSegments', []) if instant(a['start']) <= now < instant(a['end'])), None)
        # A paused block's own reserved interval is excluded from free segments.
        if allowed is None and status == 'paused' and instant(block['start']) <= now < instant(block['end']):
            allowed = {'end': min(instant(block['end']), instant(session['end'])).isoformat()}
        if allowed is None:
            raise ValueError("The room is not available now because of a conflict or your session limit.")
        if any(b.get("status") == "active" and b["id"] != block["id"] for s in result["sessions"] for b in s["blocks"]):
            raise ValueError("Finish or pause the active block first.")
        remaining = max(0, block['mins']*60-block.get('elapsedSeconds', 0))
        seconds = min(remaining, (instant(allowed['end'])-now).total_seconds())
        if seconds < 60:
            raise ValueError('Less than a minute remains in this available period.')
        block.setdefault('plannedStart', block['start'])
        block.setdefault('plannedEnd', block['end'])
        block.update(status="active", done=False, resumedAt=now.isoformat(), start=now.isoformat(),
                     end=(now+timedelta(seconds=seconds)).isoformat(),
                     mins=round((block.get('elapsedSeconds', 0)+seconds)/60, 2))
        block.setdefault("startedAt", now.isoformat())
        block.setdefault('startedRoom', session['room'])
    elif action == "pause":
        if status != "active":
            raise ValueError("Only an active block can pause.")
        elapsed = max(0, (now-instant(block.get("resumedAt", block["startedAt"]))).total_seconds())
        block["elapsedSeconds"] = min(block["mins"]*60, block.get("elapsedSeconds", 0)+elapsed)
        block.update(status="paused", pausedAt=now.isoformat())
    elif action == "complete":
        if status not in {"active", "paused"}:
            raise ValueError("Start a block before recording it as completed practice.")
        if status == "active":
            elapsed = max(0, (now-instant(block.get("resumedAt", block["startedAt"]))).total_seconds())
            block["elapsedSeconds"] = min(block["mins"]*60, block.get("elapsedSeconds", 0)+elapsed)
        block.update(status="done", done=True, completedAt=now.isoformat(), end=now.isoformat(),
                     actualMinutes=round(block.get("elapsedSeconds", 0)/60, 2))
    elif action == "skip":
        if status == "done":
            raise ValueError("Reset completed work before changing its record.")
        block.update(status="skipped", done=False, skippedAt=now.isoformat())
    elif action == "reset":
        block.update(status="planned", done=False)
        for field in ("startedAt", "resumedAt", "pausedAt", "completedAt", "skippedAt", "elapsedSeconds", "actualMinutes", 'startedRoom'):
            block.pop(field, None)
    result["lastActionAt"] = now.isoformat()
    result.pop("inputSignature", None)
    return result
