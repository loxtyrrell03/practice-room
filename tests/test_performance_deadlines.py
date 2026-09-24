import copy
import unittest
from datetime import timedelta

from academic_sessions import _candidates, reconcile
from test_academic_sessions import STATE, NOW, ACADEMIC, snapshot, booking, blocks


def event(id="class", **kw):
    return {"id": id, "pieceIds": ["a"], "date": "2026-10-01", "month": "2026-10",
            "status": "confirmed", "priority": "medium", **kw}


def weights(events, now=NOW, state=STATE):
    return [c["weight"] for c in _candidates(state, now, {**ACADEMIC, "deadlines": events})]


class PerformanceAllocationTests(unittest.TestCase):
    def test_nearer_assessment_gets_more_attention_now_than_later_major_recital(self):
        assessment = event("assessment", date=None, month="2027-02", status="provisional")
        recital = event("recital", pieceIds=["b"], date=None, month="2027-05", status="provisional", priority="high")
        for elapsed in (0, 30, 70, 110):
            with self.subTest(elapsed=elapsed):
                near, later = weights([assessment, recital], NOW + timedelta(days=elapsed))
                self.assertGreater(near, later)
        # Check the resulting practice time, not just a priority label or score.
        reservations = [booking(i, '09:00', '15:00', f'2026-09-{22+i}') for i in range(3)]
        source = snapshot(reservations, covered=['2026-09-22','2026-09-23','2026-09-24'])
        plan = reconcile(source, STATE, {**ACADEMIC, "deadlines":[assessment,recital]}, now=NOW)
        totals = {p:sum(b['mins'] for b in blocks(plan) if b.get('pieceId')==p) for p in ('a','b')}
        self.assertGreater(totals['a'], totals['b'])

    def test_importance_and_proximity_both_matter(self):
        low, medium, high = [weights([event(priority=p)])[0] for p in ("low", "medium", "high")]
        unspecified = event()
        unspecified.pop("priority")
        self.assertEqual(medium, weights([unspecified])[0])
        self.assertLess(low, medium)
        self.assertLess(medium, high)
        self.assertLessEqual(high, 5)
        far = event(date="2027-05-01", month="2027-05", priority="high")
        self.assertGreater(weights([far])[0], 1)
        self.assertGreater(weights([event()])[0], weights([far])[0])
        self.assertGreater(weights([event()], NOW + timedelta(days=5))[0], medium)

    def test_multiple_events_never_stack_and_future_recital_survives_class(self):
        recital = event("final", date="2027-05-01", month="2027-05", priority="high")
        both = [event(), recital]
        self.assertEqual(weights([event()]), weights(both))
        self.assertEqual(weights(both), weights(both + [event(str(i)) for i in range(30)]))
        after = NOW + timedelta(days=15)
        self.assertEqual(weights([recital], after), weights(both, after))
        self.assertGreater(weights(both, after)[0], weights([event()], after)[0])

    def test_scopes_are_authoritative_without_stale_piece_links(self):
        state = copy.deepcopy(STATE)
        state["pieces"][0].update(deadlineIds=["obsolete"], planning={"weight":2,"deadlineMonth":"2026-09"},
            movements=[{"id":"i"},{"id":"ii"}])
        scoped = event(movementIdsByPiece={"a":["ii"]})
        ws = weights([scoped], state=state)
        self.assertEqual(1, ws[0])
        self.assertGreater(ws[1], ws[0])
        self.assertEqual([1,1,1], weights([{**scoped,"archived":True}], state=state))
        self.assertEqual([1,1,1], weights([], state=state))

    def test_month_window_lasts_whole_month_and_timers_do_not_imply_mastery(self):
        window = event(date=None, month="2026-09", status="provisional")
        self.assertGreater(weights([window])[0], 1)
        self.assertEqual(.45, weights([window], NOW + timedelta(days=15))[0])

    def test_replanning_importance_preserves_started_and_completed_blocks(self):
        year = {**ACADEMIC,"deadlines":[event()]}
        source = snapshot()
        previous = reconcile(source, STATE, year, now=NOW)
        work = next(b for b in blocks(previous) if b["kind"]=="playing")
        work.update(status="paused", elapsedSeconds=60)
        saved = copy.deepcopy(work)
        year["deadlines"][0]["priority"] = "high"
        changed = reconcile(source, STATE, year, previous, now=NOW, force=True)
        kept = next(b for b in blocks(changed) if b["id"] == saved["id"])
        self.assertEqual(saved, kept)


if __name__ == "__main__":
    unittest.main()
