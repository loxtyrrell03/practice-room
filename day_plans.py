"""Dated practice-plan storage, validation, and calendar-day promotion."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from practice_logs import atomic_write_json, read_text


UK_TZ = ZoneInfo("Europe/London")
DAY_PLANS_REL = "data/day-plans.json"
STATE_REL = "data/state.json"


class DayPlanError(RuntimeError):
    """A coach-produced dated plan is unsafe or incomplete."""


def empty_day_plans() -> dict:
    return {"version": 1, "plans": []}


def read_json(path: Path, default):
    text = read_text(path, "")
    return json.loads(text) if text else copy.deepcopy(default)


def local_date_from_iso(value: str | None) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(UK_TZ).date().isoformat()


def next_date(value: str) -> str:
    return (datetime.fromisoformat(value).date() + timedelta(days=1)).isoformat()


def is_debrief_message(text: str | None) -> bool:
    value = " ".join(
        str(text or "").lower().replace("’", "'").split()
    )
    if not value:
        return False
    patterns = (
        r"^debrief\b",
        r"\b(?:that|this|here|it)(?:'s| is) (?:my |the )?(?:end of day )?debrief\b",
        r"\bmy (?:end of day )?debrief\b",
        r"\bend of (?:my )?practice (?:today|for the day)\b",
        r"\b(?:i(?:'m| am)|we(?:'re| are)) (?:finished|done) "
        r"(?:with )?practi[cs](?:e|ing) (?:today|for the day)\b",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def plan_for_date(document: dict, date: str) -> dict | None:
    return next(
        (
            plan
            for plan in document.get("plans") or []
            if isinstance(plan, dict) and plan.get("date") == date
        ),
        None,
    )


def validate_day_plans(document: dict) -> None:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise DayPlanError("day-plans.json must have version 1")
    plans = document.get("plans")
    if not isinstance(plans, list):
        raise DayPlanError("day-plans.json plans must be an array")
    dates = set()
    for plan in plans:
        if not isinstance(plan, dict) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", str(plan.get("date") or "")
        ):
            raise DayPlanError("each dated plan needs a valid date")
        if plan["date"] in dates:
            raise DayPlanError(f"duplicate dated plan for {plan['date']}")
        dates.add(plan["date"])
        status = plan.get("status")
        if status not in {"rough", "ready", "active", "completed"}:
            raise DayPlanError(f"invalid plan status for {plan['date']}")
        if status in {"ready", "active"}:
            _validate_ready_plan(plan)
        elif status == "rough":
            _validate_rough_plan(plan)


def _validate_ready_plan(plan: dict) -> None:
    if not str(plan.get("focus") or "").strip():
        raise DayPlanError(f"ready plan {plan['date']} needs a focus")
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise DayPlanError(f"ready plan {plan['date']} needs blocks")
    block_ids = set()
    for block in blocks:
        if not isinstance(block, dict) or not block.get("id"):
            raise DayPlanError(f"ready plan {plan['date']} has a block without an id")
        if block["id"] in block_ids:
            raise DayPlanError(f"duplicate block id {block['id']} in {plan['date']}")
        block_ids.add(block["id"])
        if block.get("done") is not False:
            raise DayPlanError(f"future block {block['id']} must have done:false")
        if not isinstance(block.get("mins"), (int, float)) or block["mins"] <= 0:
            raise DayPlanError(f"future block {block['id']} needs positive minutes")
        is_break = str(block["id"]).startswith("break")
        _validate_instruction_steps(
            block.get("steps"),
            label=f"future block {block['id']}",
            minimum=1 if is_break else 2,
            maximum=3 if is_break else 6,
        )
        if not is_break:
            leads = [str(step["lead"]).strip() for step in block["steps"]]
            if not any(
                re.match(
                    r"^\d+(?:[–-]\d+)?\s*"
                    r"(?:min|mins|minutes|reps|pass|passes|runs)\s*·\s*\S",
                    lead,
                    re.IGNORECASE,
                )
                for lead in leads
            ):
                raise DayPlanError(
                    f"future block {block['id']} needs a bold duration/reps "
                    "and technique lead"
                )
            if not leads[-1].lower().startswith(("pass when", "stop when")):
                raise DayPlanError(
                    f"future block {block['id']} must end with a Pass when "
                    "or Stop when step"
                )
        for reference in block.get("logRefs") or []:
            if not isinstance(reference, dict) or not reference.get("observationId"):
                raise DayPlanError(f"block {block['id']} has an invalid log reference")
            if not str(reference.get("note") or "").strip():
                raise DayPlanError(f"block {block['id']} has an unnamed log reference")
    for deferred in plan.get("deferredLogs") or []:
        if not isinstance(deferred, dict) or not deferred.get("observationId"):
            raise DayPlanError(f"plan {plan['date']} has an invalid deferred log")
        if not deferred.get("targetDate") or not str(
            deferred.get("reason") or ""
        ).strip():
            raise DayPlanError(
                f"deferred log in {plan['date']} needs a target date and reason"
            )


def _validate_instruction_steps(
    steps,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> None:
    if not isinstance(steps, list) or not minimum <= len(steps) <= maximum:
        raise DayPlanError(
            f"{label} needs {minimum}–{maximum} short instruction bullets"
        )
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise DayPlanError(f"{label} step {index} must be an object")
        lead = str(step.get("lead") or "").strip()
        text = str(step.get("text") or "").strip()
        if not 2 <= len(lead) <= 60:
            raise DayPlanError(f"{label} step {index} needs a short bold lead")
        if not 5 <= len(text) <= 240:
            raise DayPlanError(
                f"{label} step {index} needs concise plain-language instructions"
            )


def _validate_rough_plan(plan: dict) -> None:
    if "preview" in plan:
        raise DayPlanError(
            f"rough plan {plan['date']} must use outline bullets, not preview prose"
        )
    if plan.get("outline") is not None:
        _validate_instruction_steps(
            plan["outline"],
            label=f"rough plan {plan['date']}",
            minimum=2,
            maximum=6,
        )


def accounted_observation_ids(plan: dict) -> list[str]:
    accounted = []
    for block in plan.get("blocks") or []:
        for reference in block.get("logRefs") or []:
            observation_id = reference.get("observationId")
            if observation_id:
                accounted.append(str(observation_id))
    for deferred in plan.get("deferredLogs") or []:
        observation_id = deferred.get("observationId")
        if observation_id:
            accounted.append(str(observation_id))
    return accounted


def validate_coach_day_change(
    *,
    before_state: dict,
    after_state: dict,
    before_plans: dict,
    after_plans: dict,
    job: dict,
    batch: dict,
) -> None:
    validate_day_plans(after_plans)
    target_date = local_date_from_iso(job.get("acceptedAt"))
    before_today = str((before_state.get("today") or {}).get("date") or "")
    after_today = str((after_state.get("today") or {}).get("date") or "")
    if before_today <= target_date and after_today > target_date:
        raise DayPlanError(
            "the coach put a future plan into state.today; write it to "
            "data/day-plans.json instead"
        )
    if before_today > target_date and after_today != before_today:
        raise DayPlanError(
            "the active day advanced while an older queued coach turn was "
            "retrying; its date cannot be changed"
        )
    if before_today == target_date and after_today != before_today:
        raise DayPlanError(
            "state.today is the active calendar day and cannot be replaced "
            "during a same-day coach turn"
        )

    tomorrow = next_date(target_date)
    before_plan = plan_for_date(before_plans, tomorrow)
    after_plan = plan_for_date(after_plans, tomorrow)
    plan_changed = after_plan != before_plan
    expected = {
        str(observation_id)
        for observation_id in (
            list(batch.get("routeObservationIds") or [])
            + list(batch.get("reviewObservationIds") or [])
        )
    }
    if not plan_changed or not after_plan or after_plan.get("status") != "ready":
        return
    accounted = accounted_observation_ids(after_plan)
    duplicates = sorted(
        observation_id
        for observation_id in set(accounted)
        if accounted.count(observation_id) > 1
    )
    missing = sorted(expected - set(accounted))
    if duplicates:
        raise DayPlanError(
            "tomorrow's plan accounts for observation IDs more than once: "
            + ", ".join(duplicates)
        )
    if missing:
        raise DayPlanError(
            "tomorrow's plan must explicitly schedule or defer every named "
            "practice log: "
            + ", ".join(missing)
        )


def promote_due_plan(data_dir: str | Path, now: datetime | None = None) -> dict:
    root = Path(data_dir)
    current = (now or datetime.now(timezone.utc)).astimezone(UK_TZ)
    current_date = current.date().isoformat()
    state_path = root / STATE_REL
    plans_path = root / DAY_PLANS_REL
    state = read_json(state_path, {})
    today_date = str((state.get("today") or {}).get("date") or "")
    if not today_date or today_date >= current_date:
        return {"status": "current", "date": today_date}

    plans = read_json(plans_path, empty_day_plans())
    validate_day_plans(plans)
    due = plan_for_date(plans, current_date)
    if not due or due.get("status") not in {"ready", "active"}:
        return {
            "status": "missing",
            "date": current_date,
            "previousDate": today_date,
        }

    state["today"] = {
        "date": due["date"],
        "focus": due["focus"],
        "blocks": copy.deepcopy(due["blocks"]),
    }
    for block in state["today"]["blocks"]:
        block["done"] = False
    due["status"] = "active"
    due["activatedAt"] = current.isoformat(timespec="seconds")
    atomic_write_json(state_path, state)
    atomic_write_json(plans_path, plans)
    return {"status": "promoted", "date": current_date}
