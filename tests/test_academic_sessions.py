import copy
from datetime import datetime, timedelta
import unittest

from academic_sessions import reconcile, overview, apply_action, instant, _candidates

NOW = instant('2026-09-22T11:00:00+01:00')
ACADEMIC = {'dailyTargetMinutes': {'min': 240, 'max': 360}}
STATE = {'pieces': [
    {'id': 'a', 'title': 'Piece A', 'planning': {'weight': 1, 'focus': 'Phrase A'}},
    {'id': 'b', 'title': 'Piece B', 'planning': {'weight': 1, 'focus': 'Phrase B'}},
]}


def booking(event_id=1, start='12:00', end='14:00', day='2026-09-22'):
    return {'id': f'asimut:{event_id}', 'eventId': event_id, 'date': day,
            'startTime': start, 'endTime': end, 'room': 'Room A', 'bookedMinutes': 120}


def snapshot(rows=None, *, now=NOW, conflicts=None, covered=None, status='ready'):
    return {'status': status, 'observedAt': now.isoformat(), 'completeCoveredDates': covered or ['2026-09-22','2026-09-23'],
            'reservations': [booking()] if rows is None else rows, 'conflicts': conflicts or []}


def blocks(document, *, planned_only=False):
    return [b for s in document['sessions'] for b in s['blocks'] if not planned_only or b['status'] == 'planned']


def work_minutes(document):
    return sum(b['mins'] for b in blocks(document, planned_only=True) if b['kind'] in {'playing','study'})


class AllocationTests(unittest.TestCase):
    def assert_bounds(self, document):
        intervals = []
        ids = []
        for session in document['sessions']:
            ids.append(session['id'])
            for b in session['blocks']:
                if b['status'] != 'planned':
                    continue
                start, end = instant(b['start']), instant(b['end'])
                self.assertGreater(end, start)
                self.assertGreaterEqual(start, instant(session['start']))
                self.assertLessEqual(end, instant(session['end']))
                self.assertTrue(any(instant(s['start']) <= start and end <= instant(s['end']) for s in session['usableSegments']))
                intervals.append((start,end,b['id']))
        self.assertEqual(len(ids),len(set(ids)))
        intervals.sort()
        for before, after in zip(intervals, intervals[1:]):
            self.assertLessEqual(before[1],after[0], (before,after))

    def test_allocation_with_breaks_never_fills_unbooked_target(self):
        doc = reconcile(snapshot(), STATE, ACADEMIC, now=NOW)
        self.assert_bounds(doc)
        self.assertLess(work_minutes(doc),120)
        self.assertTrue(any(b['kind']=='break' for b in blocks(doc)))
        stats = overview(doc, ACADEMIC, now=NOW)
        self.assertGreater(stats['days'][0]['targetGapMinutes'],120)
        self.assertEqual(0,stats['days'][1]['bookedMinutes'])

    def test_lesson_subtracts_exact_middle_segment(self):
        doc = reconcile(snapshot(conflicts=[booking(77,'12:45','13:15')]), STATE, ACADEMIC, now=NOW)
        self.assert_bounds(doc)
        for b in blocks(doc,planned_only=True):
            self.assertFalse(instant(b['start']) < instant('2026-09-22T13:15') and instant(b['end']) > instant('2026-09-22T12:45'))
        self.assertEqual(90,doc['sessions'][0]['availableMinutes'])

    def test_overlap_never_counts_or_allocates_twice(self):
        doc = reconcile(snapshot([booking(), booking(2,'13:00','15:00')]), STATE, ACADEMIC, now=NOW)
        self.assert_bounds(doc)
        self.assertEqual(180,overview(doc,ACADEMIC,now=NOW)['days'][0]['bookedMinutes'])

    def test_expansion_and_shortening_rebuild_unfinished(self):
        short = reconcile(snapshot([booking(end='13:00')]),STATE,ACADEMIC,now=NOW)
        long = reconcile(snapshot(),STATE,ACADEMIC,short,now=NOW)
        self.assertGreater(work_minutes(long),work_minutes(short))
        shortened = reconcile(snapshot([booking(end='12:30')]),STATE,ACADEMIC,long,now=NOW)
        self.assertLess(work_minutes(shortened),work_minutes(long))
        self.assert_bounds(shortened)

    def test_manual_limit_zero_and_daily_override(self):
        off = reconcile(snapshot(),STATE,{**ACADEMIC,'sessionLimits':{'asimut:1':0}},now=NOW)
        self.assertEqual([],blocks(off))
        limit = reconcile(snapshot(),STATE,{**ACADEMIC,'sessionLimits':{'asimut:1':35}},now=NOW)
        self.assert_bounds(limit)
        self.assertLessEqual(sum(b['mins'] for b in blocks(limit)),35)
        capped = reconcile(snapshot(),STATE,{**ACADEMIC,'dailyOverrides':{'2026-09-22':{'maxMinutes':30}}},now=NOW)
        self.assertLessEqual(work_minutes(capped),30)

    def test_current_booking_uses_only_remaining_time(self):
        now=instant('2026-09-22T13:33:25')
        doc=reconcile(snapshot(now=now),STATE,ACADEMIC,now=now)
        self.assert_bounds(doc)
        self.assertTrue(all(instant(b['start']) >= now for b in blocks(doc)))
        self.assertLess(work_minutes(doc),27)

    def test_stale_does_not_erase_or_confirm_capacity(self):
        doc=reconcile(snapshot(),STATE,ACADEMIC,now=NOW)
        stale=reconcile(snapshot(rows=[],status='stale'),STATE,ACADEMIC,doc,now=NOW)
        self.assertEqual(doc['sessions'],stale['sessions'])
        self.assertEqual('stale',stale['sync']['status'])
        aged=overview(doc,ACADEMIC,now=NOW+timedelta(minutes=31))
        self.assertEqual('stale',aged['sync']['status'])
        self.assertFalse(aged['days'][0]['availabilityConfirmed'])

    def test_cancellation_does_not_become_completion(self):
        doc=reconcile(snapshot(),STATE,ACADEMIC,now=NOW)
        cancelled=reconcile(snapshot(rows=[]),STATE,ACADEMIC,doc,now=NOW)
        self.assertEqual('cancelled',cancelled['sessions'][0]['bookingStatus'])
        self.assertEqual([],blocks(cancelled))
        self.assertEqual(0,overview(cancelled,ACADEMIC,now=NOW)['days'][0]['doneMinutes'])

    def test_unknown_coverage_does_not_cancel(self):
        doc=reconcile(snapshot(),STATE,ACADEMIC,now=NOW)
        changed=reconcile(snapshot(rows=[],covered=['2026-09-23']),STATE,ACADEMIC,doc,now=NOW)
        self.assertEqual('unverified',changed['sessions'][0]['bookingStatus'])
        self.assertEqual(doc['sessions'][0]['blocks'],changed['sessions'][0]['blocks'])

    def test_past_plans_become_missed_never_done(self):
        doc=reconcile(snapshot(),STATE,ACADEMIC,now=NOW)
        later=instant('2026-09-22T15:00')
        past=reconcile(snapshot(now=later),STATE,ACADEMIC,doc,now=later)
        self.assertTrue(all(b['status']=='missed' and not b['done'] for b in blocks(past)))

    def test_shift_of_old_ended_booking_keeps_one_session(self):
        doc=reconcile(snapshot(),STATE,ACADEMIC,now=NOW)
        later=instant('2026-09-22T15:00')
        shifted=reconcile(snapshot([booking(start='16:00',end='17:00')],now=later),STATE,ACADEMIC,doc,now=later)
        self.assertEqual(1,len(shifted['sessions']))
        self.assertTrue(shifted['sessions'][0]['start'].startswith('2026-09-22T16:00'))
        self.assert_bounds(shifted)


class ProgressTests(unittest.TestCase):
    def setup_live(self):
        now=instant('2026-09-22T12:00')
        doc=reconcile(snapshot(now=now),STATE,ACADEMIC,now=now)
        target=next(b for b in blocks(doc) if b['kind']=='playing')
        payload={'sessionId':'asimut:1','blockId':target['id']}
        return now,doc,payload

    def test_early_start_fits_real_room_without_waiting_for_block(self):
        now,doc,payload=self.setup_live()
        active=apply_action(doc,{**payload,'action':'start'},now=now)
        block=next(b for b in blocks(active) if b['id']==payload['blockId'])
        self.assertEqual(now,instant(block['start']))
        replanned=reconcile(snapshot(now=now),STATE,ACADEMIC,active,now=now)
        kept=next(b for b in blocks(replanned) if b['id']==payload['blockId'])
        self.assertEqual(block,kept)
        for b in blocks(replanned,planned_only=True):
            self.assertTrue(instant(b['end']) <= instant(kept['start']) or instant(b['start']) >= instant(kept['end']))

    def test_pause_resume_elapsed_and_completion_not_full_fake_dose(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        doc=apply_action(doc,{**payload,'action':'pause'},now=now+timedelta(minutes=3))
        paused=next(b for b in blocks(doc) if b['id']==payload['blockId'])
        self.assertEqual(180,paused['elapsedSeconds'])
        doc=apply_action(doc,{**payload,'action':'resume'},now=now+timedelta(minutes=8))
        doc=apply_action(doc,{**payload,'action':'complete'},now=now+timedelta(minutes=10))
        done=next(b for b in blocks(doc) if b['id']==payload['blockId'])
        self.assertEqual(5,done['actualMinutes'])
        self.assertEqual(5,overview(doc,ACADEMIC,now=now+timedelta(minutes=10))['days'][0]['doneMinutes'])

    def test_completed_preserved_after_change_remaining_redistributed(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        now+=timedelta(minutes=10)
        doc=apply_action(doc,{**payload,'action':'complete'},now=now)
        changed=reconcile(snapshot([booking(end='13:00')],now=now),STATE,ACADEMIC,doc,now=now)
        self.assertEqual(1,len([b for b in blocks(changed) if b['id']==payload['blockId']]))
        self.assertTrue(any(b['status']=='planned' for b in blocks(changed)))
        self.assert_bounds_planned(changed)

    def assert_bounds_planned(self,doc):
        for s in doc['sessions']:
            for b in s['blocks']:
                if b['status']=='planned': self.assertLessEqual(instant(b['end']),instant(s['end']))

    def test_cancel_active_preserves_actual_record_and_blocks_start(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        cancelled=reconcile(snapshot(rows=[],now=now),STATE,ACADEMIC,doc,now=now)
        self.assertEqual('active',blocks(cancelled)[0]['status'])
        self.assertEqual('cancelled',cancelled['sessions'][0]['bookingStatus'])

    def test_changed_booking_around_active_signals_attention(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        changed=reconcile(snapshot([booking(end='12:15')],now=now),STATE,ACADEMIC,doc,now=now)
        self.assertTrue(changed['sessions'][0]['needsAttention'])
        self.assertEqual('active',next(b for b in blocks(changed) if b['id']==payload['blockId'])['status'])

    def test_room_change_warning_persists_across_refreshes(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        moved=booking()
        moved['room']='Room B'
        once=reconcile(snapshot([moved],now=now),STATE,ACADEMIC,doc,now=now)
        twice=reconcile(snapshot([moved],now=now),STATE,ACADEMIC,once,now=now,force=True)
        self.assertTrue(twice['sessions'][0]['needsAttention'])

    def test_completed_in_cancelled_booking_still_consumes_daily_cap(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        now+=timedelta(minutes=20)
        doc=apply_action(doc,{**payload,'action':'complete'},now=now)
        year={**ACADEMIC,'dailyOverrides':{'2026-09-22':{'maxMinutes':30}}}
        rebuilt=reconcile(snapshot([booking(2,start='14:00',end='15:00')],now=now),STATE,year,doc,now=now)
        self.assertLessEqual(work_minutes(rebuilt),10)
        self.assertEqual(20,overview(rebuilt,year,now=now)['days'][0]['doneMinutes'])

    def test_completed_and_active_count_toward_cap(self):
        now,doc,payload=self.setup_live()
        doc=apply_action(doc,{**payload,'action':'start'},now=now)
        capped=reconcile(snapshot(now=now),STATE,{**ACADEMIC,'dailyOverrides':{'2026-09-22':{'maxMinutes':30}}},doc,now=now)
        allocated=sum(b['mins'] for b in blocks(capped) if b['kind'] in {'playing','study'} and b['status'] in {'active','planned'})
        self.assertLessEqual(allocated,30)

    def test_future_outside_stale_and_unstarted_complete_rejected(self):
        now,doc,payload=self.setup_live()
        for action,when in [('start',now-timedelta(days=1)),('start',now+timedelta(hours=3)),('complete',now)]:
            with self.assertRaises(ValueError): apply_action(doc,{**payload,'action':action},now=when)
        with self.assertRaises(ValueError): apply_action(doc,{**payload,'action':'start'},now=now+timedelta(minutes=31))


class PrioritiesTests(unittest.TestCase):
    def test_scaling_every_piece_weight_does_not_change_allocations(self):
        state=copy.deepcopy(STATE)
        state['pieces'][0]['planning']['weight']=3
        rows=[booking(n+1,day=f'2026-09-{22+n:02}') for n in range(8)]
        source=snapshot(rows,covered=[row['date'] for row in rows])
        base=reconcile(source,state,ACADEMIC,now=NOW)
        assignments=lambda doc:[(b.get('pieceId'),b.get('movementId'),b['start'],b['mins']) for b in blocks(doc)]
        for factor in (.01,100):
            scaled=copy.deepcopy(state)
            for p in scaled['pieces']: p['planning']['weight']*=factor
            self.assertEqual(assignments(base),assignments(reconcile(source,scaled,ACADEMIC,now=NOW)))

    def test_seven_work_eleven_playing_movements_plus_lecture_get_coverage(self):
        works=[]
        for index,count in enumerate((4,3,1,1,1,1,1)):
            works.append({'id':f'piece-{index}','title':f'Work {index}',
                          'planning':{'weight':(3 if index<2 else 1),'offBench':index==6},
                          'movements':[{'id':f'mvt-{m+1}','planning':{'weight':1/count}} for m in range(count)]})
        rows=[booking(n+1,day=f'2026-09-{22+n:02}') for n in range(8)]
        doc=reconcile(snapshot(rows,covered=[row['date'] for row in rows]),{'pieces':works},ACADEMIC,now=NOW)
        played={(b.get('pieceId'),b.get('movementId')) for b in blocks(doc) if b['kind'] in {'playing','study'}}
        self.assertEqual(12,len(played))
        self.assertEqual(11,len({(b.get('pieceId'),b.get('movementId')) for b in blocks(doc) if b['kind']=='playing'}))

    def test_two_piece_weights_change_actual_share_not_forced_alternation(self):
        rows=[booking(n+1,day=f'2026-09-{22+n:02}') for n in range(9)]
        source=snapshot(rows,covered=[row['date'] for row in rows])
        equal=reconcile(source,STATE,ACADEMIC,now=NOW)
        weighted=copy.deepcopy(STATE)
        weighted['pieces'][0]['planning']['weight']=10
        unequal=reconcile(source,weighted,ACADEMIC,now=NOW)
        totals=lambda doc:{pid:sum(b['mins'] for b in blocks(doc) if b.get('pieceId')==pid) for pid in ('a','b')}
        self.assertGreater(totals(unequal)['a'],1.5*totals(unequal)['b'])
        self.assertGreater(totals(unequal)['a'],totals(equal)['a'])

    def test_confirmed_exact_deadline_changes_priority_once(self):
        state=copy.deepcopy(STATE)
        state['pieces'][0]['deadlineIds']=['soon','later']
        year={**ACADEMIC,'deadlines':[{'id':'soon','date':'2026-10-01','month':'2027-05','status':'confirmed'},
                                    {'id':'later','month':'2027-05','status':'provisional'}]}
        candidates=_candidates(state,NOW,year)
        self.assertEqual(2,len(candidates))
        self.assertGreater(candidates[0]['weight'], 1)
        single={**year,'deadlines':year['deadlines'][:1]}
        self.assertEqual(candidates[0]['weight'],_candidates(state,NOW,single)[0]['weight'])
        self.assertEqual(1,candidates[1]['weight'])

    def test_movement_scope_and_total_work_weight(self):
        state={'pieces':[{'id':'sonata','title':'Sonata','deadlineIds':['feb','may'],'planning':{'weight':3},
            'movements':[{'id':'i','planning':{'weight':1}}, {'id':'ii','planning':{'weight':1}}]}]}
        year={'deadlines':[{'id':'feb','month':'2026-10','movementIdsByPiece':{'sonata':['i']}},
                           {'id':'may','month':'2027-05','movementIdsByPiece':{'sonata':['i','ii']}}]}
        candidates=_candidates(state,NOW,year)
        self.assertGreater(candidates[0]['weight'],candidates[1]['weight'])
        self.assertGreater(candidates[1]['weight'],1.5)

    def test_recent_completed_work_moves_allocation_to_other_piece(self):
        prior={'sessions':[{'id':'past','date':'2026-09-21','start':'2026-09-21T12:00+01:00','end':'2026-09-21T14:00+01:00','room':'Room A','bookingStatus':'confirmed','bookedMinutes':120,
            'blocks':[{'id':'done','kind':'playing','pieceId':'a','movementId':None,'mins':120,'actualMinutes':120,'status':'done','done':True,'start':'2026-09-21T12:00+01:00','end':'2026-09-21T14:00+01:00','completedAt':'2026-09-21T14:00+01:00'}]}]}
        doc=reconcile(snapshot(),STATE,ACADEMIC,prior,now=NOW)
        first=next(b for b in blocks(doc,planned_only=True) if b['kind']=='playing')
        self.assertEqual('b',first['pieceId'])


if __name__=='__main__': unittest.main()
