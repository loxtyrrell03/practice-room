import copy
import tempfile
import threading
import unittest
from pathlib import Path

from academic_sessions import reconcile, instant
from practice_logs import atomic_write_json
from practice_notebook import PracticeNotebook, preparation_routes, suggestions
from test_academic_sessions import STATE, ACADEMIC, NOW, snapshot, booking, blocks


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = copy.deepcopy(STATE)
        self.state['pieces'][0]['movements'] = [{'id': 'one', 'title': 'I. First'}]
        self.year = {**ACADEMIC, 'startDate': '2026-09-22', 'deadlines': [
            {'id': 'winter', 'month': '2027-02', 'date': None, 'pieceIds': ['a'], 'movementIdsByPiece': {'a': ['one']}},
            {'id': 'spring', 'month': '2027-05', 'date': '2027-05-14', 'pieceIds': ['a', 'b']}]}
        atomic_write_json(self.root/'data/state.json', self.state)
        atomic_write_json(self.root/'data/academic-year.json', self.year)
        self.book = PracticeNotebook(self.root, threading.RLock(), lambda: NOW)

    def note(self, **kw):
        return self.book.note({'clientId': 'unique', 'pieceId': 'a', 'movementId': 'one', 'text': 'bar 23 uneven', **kw})

    def test_note_without_booking_is_atomic_and_retry_survives_restart(self):
        first = self.note()
        self.assertEqual(1, len(first['notebook']['tasks']))
        restarted = PracticeNotebook(self.root, threading.RLock(), lambda: NOW)
        again = restarted.note({'clientId': 'unique', 'pieceId': 'a', 'movementId': 'one', 'text': 'bar 23 uneven'})
        self.assertFalse(again['created'])
        self.assertEqual(first['note'], again['note'])
        self.assertEqual(1, len(restarted.get()['tasks']))
        with self.assertRaises(ValueError):
            self.note(text='different note, same identity')

    def test_dismissal_and_manual_edits_survive_new_notes_and_refresh(self):
        task = self.note()['notebook']['tasks'][0]
        doc = self.book.task({'id': task['id'], 'revision': 1, 'title': 'My chosen approach', 'status': 'dismissed', 'minutes': 15})
        self.note(clientId='another', text='bar 23 still uneven')
        task = self.book.get()['tasks'][0]
        self.assertEqual('dismissed', task['status'])
        self.assertEqual('My chosen approach', task['title'])
        self.assertEqual(2, len(task['noteIds']))
        planned = reconcile(snapshot(), self.state, self.year, tasks=[task], now=NOW)
        self.assertFalse(any(b.get('taskId') for b in blocks(planned)))
        with self.assertRaises(ValueError):
            self.book.task({'id': task['id'], 'revision': 1, 'status': 'open'})
        restored = self.book.task({'id': task['id'], 'revision': task['revision'], 'status': 'open'})
        self.assertEqual('open', restored['tasks'][0]['status'])

    def test_note_task_is_scheduled_once_per_day_and_scaled_to_capacity(self):
        task = self.note()['notebook']['tasks'][0]
        task['minutes'] = 30
        plan = reconcile(snapshot([booking(end='12:20'), booking(2, '15:00', '17:00')]), self.state, self.year, tasks=[task], now=NOW)
        linked = [b for b in blocks(plan) if b.get('taskId') == task['id']]
        self.assertEqual(1, len(linked))
        self.assertLessEqual(linked[0]['mins'], 30)
        self.assertIn('comfortable tempo', linked[0]['steps'][0]['text'])
        self.assertEqual('23', task['bars'])
        # A cancellation redistributes the open task without losing its note.
        replanned = reconcile(snapshot([booking(2, '15:00', '17:00')]), self.state, self.year, plan, tasks=[task], now=NOW)
        self.assertEqual(1, sum(bool(b.get('taskId')) for b in blocks(replanned)))
        self.assertEqual(1, len(self.book.get()['notes']))

    def test_completed_task_remains_evidence_and_is_not_resolved_by_timer(self):
        task = self.note()['notebook']['tasks'][0]
        plan = reconcile(snapshot(), self.state, self.year, tasks=[task], now=NOW)
        block = next(b for b in blocks(plan) if b.get('taskId'))
        block.update(done=True, status='done', actualMinutes=5, completedAt=block['end'])
        again = reconcile(snapshot(), self.state, self.year, plan, tasks=[task], now=NOW)
        self.assertEqual(1, sum(bool(b.get('taskId')) for b in blocks(again)))
        self.assertEqual('open', self.book.get()['tasks'][0]['status'])

    def test_notes_are_scoped_and_invalid_writes_preserve_data(self):
        self.note()
        original = self.book.get()
        for payload in [{'pieceId': 'unknown'}, {'movementId': 'other'}, {'text': ''}]:
            with self.assertRaises(ValueError):
                self.note(clientId='bad', **payload)
        self.assertEqual(original, self.book.get())
        self.note(clientId='other-piece', pieceId='b', movementId=None)
        self.assertEqual(2, len(self.book.get()['tasks']))

    def test_memory_fingering_and_positive_notes(self):
        self.assertEqual('study', suggestions({'text': 'memory shaky', 'bars': '20–30'})[0]['kind'])
        self.assertEqual('fingering', suggestions({'text': 'fingering undecided bars 20-30'})[0]['category'])
        self.assertEqual([], suggestions({'text': 'bar 23 no longer uneven'}))
        self.assertEqual([], suggestions({'text': 'Good lesson today'}))

    def test_not_before_and_resolved_tasks_do_not_get_scheduled(self):
        task = self.note()['notebook']['tasks'][0]
        task['notBefore'] = '2026-09-23'
        plan = reconcile(snapshot(), self.state, self.year, tasks=[task], now=NOW)
        self.assertFalse(any(b.get('taskId') for b in blocks(plan)))
        task.update(notBefore='2026-09-22', status='resolved')
        plan = reconcile(snapshot(), self.state, self.year, tasks=[task], now=NOW)
        self.assertFalse(any(b.get('taskId') for b in blocks(plan)))

    def test_timeline_scope_windows_and_edits_are_durable(self):
        doc = self.book.get()
        self.assertEqual(3, len(doc['routes']))
        winter = doc['routes'][0]
        self.assertIsNone(winter['deadlineDate'])
        self.assertEqual('2027-02-28', winter['stages'][-1]['endDate'])
        self.assertEqual(['one'], winter['stages'][0]['movementIds'])
        stage = winter['stages'][0]
        updated = self.book.stage({**stage, 'revision': doc['revision'], 'title': 'My section plan', 'focus': 'A specific passage', 'checkpoint': 'My check'})
        self.assertEqual('My section plan', updated['routes'][0]['stages'][0]['title'])
        with self.assertRaises(ValueError):
            self.book.stage({**stage, 'revision': doc['revision']})
        self.assertEqual('My section plan', self.book.get()['routes'][0]['stages'][0]['title'])
        planned = reconcile(snapshot(), self.state, self.year, routes=updated['routes'], now=NOW)
        matching = [b for b in blocks(planned) if b.get('pieceId') == 'a']
        self.assertTrue(matching)
        self.assertTrue(all('A specific passage' in b['steps'][0]['text'] for b in matching))

    def test_legacy_notes_stay_visible_and_corrupt_storage_fails_closed(self):
        atomic_write_json(self.root/'data/observations.json', {'obs': [{'id': 'old', 'pieceId': 'a', 'localDate': '2026-09-22', 'text': 'Old note'}]})
        self.assertEqual('old', self.book.get()['history'][0]['id'])
        path = self.root/'data/notebook.json'
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.note()
        self.assertEqual('{broken', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
