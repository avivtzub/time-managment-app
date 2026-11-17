"""Offline backend tests: in-memory SQLite and fake Google responses only."""
import unittest
from datetime import datetime, timedelta, time
from unittest.mock import patch

from fastapi import HTTPException
from googleapiclient.errors import HttpError
from sqlmodel import SQLModel, Session, create_engine, select
import main

class Response:
    def __init__(self, result): self.result = result
    def execute(self):
        if isinstance(self.result, Exception): raise self.result
        return self.result

class FakeGoogle:
    def __init__(self, calendars=None, events=None):
        self.calendar_pages = calendars or {None: {'items': [{'id': 'primary', 'primary': True}]}}
        self.event_pages = events or {}
        self.remote = {}
        self.insert_calls = []
        self.lose_response = False
    def calendarList(self):
        parent = self
        class Calendars:
            def list(self, **kwargs): return Response(parent.calendar_pages[kwargs.get('pageToken')])
        return Calendars()
    def events(self):
        parent = self
        class Events:
            def list(self, calendarId, **kwargs):
                return Response(parent.event_pages.get((calendarId, kwargs.get('pageToken')), {'items': []}))
            def get(self, calendarId, eventId):
                if eventId in parent.remote: return Response(parent.remote[eventId])
                return Response(HttpError(type('R', (), {'status': 404, 'reason': 'Not Found'})(), b'{}'))
            def insert(self, calendarId, body):
                parent.insert_calls.append(body)
                parent.remote[body['id']] = body
                if parent.lose_response:
                    parent.lose_response = False
                    return Response(ConnectionError('response lost'))
                return Response(body)
        return Events()

class BackendTests(unittest.TestCase):
    def test_hebrew_date_overlay_does_not_block_evening_but_stays_visible(self):
        overlay = '#hebrew@group.v.calendar.google.com'
        event = {'id': 'hebrew-date', 'summary': '8 Kislev 5787',
                 'start': {'date': str(self.day)},
                 'end': {'date': str(self.day + timedelta(days=1))}}
        google = FakeGoogle(
            calendars={None: {'items': [{'id': 'primary', 'primary': True}, {'id': overlay}]}},
            events={(overlay, None): {'items': [event]}})
        item = self.add(target_date=str(self.day), preferred_time='Evening',
                        energy_level=3, duration_minutes=60)
        with patch.object(main, 'calendar_service', return_value=google):
            result = main.schedule_tasks(self.session)
        self.session.refresh(item)
        self.assertEqual(result['scheduled_count'], 1)
        self.assertEqual(item.start_time, datetime.combine(self.day, time(17)))
        visible = main.read_calendar(google, self.start, self.start + timedelta(days=1),
                                     include_nonblocking=True)
        self.assertEqual([event['id'] for event in visible], ['hebrew-date'])

    def setUp(self):
        self.engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.now = main.datetime.now(main.TZ).replace(tzinfo=None)
        self.day = self.now.date() + timedelta(days=2)
        self.start = datetime.combine(self.day, time(15))
    def tearDown(self):
        self.session.close()
        self.engine.dispose()
    def add(self, **kwargs):
        data = dict(title='Task', duration_minutes=60, preferred_time='Any', priority=2)
        data.update(kwargs)
        task = main.Task(**data)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def test_calendar_paginates_both_resources_and_honors_exclusive_all_day_end(self):
        event = {'id': 'e', 'start': {'date': str(self.day)},
                 'end': {'date': str(self.day + timedelta(days=2))}}
        google = FakeGoogle(
            calendars={None: {'items': [{'id': 'one'}], 'nextPageToken': 'two'},
                       'two': {'items': [{'id': 'two'}]}},
            events={('one', None): {'items': [], 'nextPageToken': 'next'},
                    ('one', 'next'): {'items': [event]},
                    ('two', None): {'items': [dict(event, id='second')]}})
        events = main.read_calendar(google, self.start, self.start + timedelta(days=3))
        self.assertEqual({item['id'] for item in events}, {'e', 'second'})
        self.assertEqual(events[0]['end'], datetime.combine(self.day + timedelta(days=2), time.min))

    def test_nonblocking_events_are_visible_but_do_not_block(self):
        base = {'id': 'transparent', 'start': {'date': str(self.day)},
                'end': {'date': str(self.day + timedelta(days=1))}}
        google = FakeGoogle(events={('primary', None): {'items': [
            dict(base, transparency='transparent'),
            dict(base, id='declined', attendees=[{'self': True, 'responseStatus': 'declined'}]),
            dict(base, id='cancelled', status='cancelled')]}})
        blocked = main.read_calendar(google, self.start, self.start+timedelta(days=1))
        visible = main.read_calendar(google, self.start, self.start+timedelta(days=1), include_nonblocking=True)
        self.assertEqual(blocked, [])
        self.assertEqual({event['id'] for event in visible}, {'transparent', 'declined'})
    def test_calendar_read_failure_preserves_existing_proposal(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        google = FakeGoogle(events={('primary', None): ConnectionError('offline')})
        with patch.object(main, 'calendar_service', return_value=google):
            with self.assertRaises(HTTPException) as error:
                main.schedule_tasks(self.session)
        self.assertEqual(error.exception.status_code, 503)
        self.session.refresh(item)
        self.assertEqual((item.status, item.start_time), ('scheduled', self.start))

    def test_full_tomorrow_moves_flexible_task_to_next_day(self):
        tomorrow = self.now.date() + timedelta(days=1)
        google = FakeGoogle(events={('primary', None): {'items': [
            {'id': 'busy', 'start': {'date': str(tomorrow)},
             'end': {'date': str(tomorrow + timedelta(days=1))}}]}})
        item = self.add()
        with patch.object(main, 'calendar_service', return_value=google):
            result = main.schedule_tasks(self.session)
        self.session.refresh(item)
        self.assertEqual(result['scheduled_count'], 1)
        self.assertEqual(item.start_time.date(), tomorrow + timedelta(days=1))

    def test_locked_proposal_survives_replanning_and_blocks_other_tasks(self):
        locked = self.add(status='scheduled', locked=True, start_time=self.start,
                          end_time=self.start+timedelta(hours=1))
        fixed = self.add(target_date=str(self.day), preferred_start_time='15:00', time_mode='fixed')
        with patch.object(main, 'calendar_service', return_value=FakeGoogle()):
            result = main.schedule_tasks(self.session)
        self.session.refresh(locked)
        self.session.refresh(fixed)
        self.assertEqual(locked.start_time, self.start)
        self.assertEqual(fixed.status, 'new')
        self.assertEqual(len(result['unscheduled']), 1)

    def test_lost_insert_response_retry_recovers_without_duplicate(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        google = FakeGoogle()
        google.lose_response = True
        with patch.object(main, 'calendar_service', return_value=google):
            first = main.sync_to_google(self.session)
            self.assertEqual(len(first['failed']), 1)
            self.session.refresh(item)
            identity = item.google_event_id
            self.assertIsNotNone(identity)
            second = main.sync_to_google(self.session)
        self.assertEqual(second['synced_count'], 1)
        self.assertEqual(len(google.insert_calls), 1)
        self.assertEqual(item.google_event_id, identity)

    def test_bad_task_and_time_ranges_are_rejected(self):
        for value in (0, -1, 841):
            with self.assertRaises(ValueError):
                main.TaskInput(title='Task', duration_minutes=value)
        with self.assertRaises(ValueError):
            main.TaskInput(title='Task', duration_minutes=60, preferred_start_time='25:00')
        with self.assertRaises(ValueError):
            main.UpdateTaskTimeRequest(start_time=self.start, end_time=self.start)



class RecoveryTests(unittest.TestCase):
    setUp = BackendTests.setUp
    tearDown = BackendTests.tearDown
    add = BackendTests.add
    def test_conflict_before_insert_leaves_proposal_editable(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        event = {'id': 'conflict', 'start': {'dateTime': main.local(self.start).isoformat()},
                 'end': {'dateTime': main.local(self.start+timedelta(hours=1)).isoformat()}}
        google = FakeGoogle(events={('primary', None): {'items': [event]}})
        with patch.object(main, 'calendar_service', return_value=google):
            result = main.sync_to_google(self.session)
        self.session.refresh(item)
        self.assertEqual(len(result['failed']), 1)
        self.assertIsNone(item.google_event_id)
        self.assertEqual(item.status, 'scheduled')
        self.assertEqual(google.insert_calls, [])

    def test_partial_sync_commits_success_and_retries_only_failure(self):
        good = self.add(title='Good', status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        bad = self.add(title='Bad', status='scheduled', start_time=self.start+timedelta(hours=2),
                       end_time=self.start+timedelta(hours=3))
        google = FakeGoogle()
        original = google.events
        def resources():
            resource = original()
            insert = resource.insert
            def fail_bad(calendarId, body):
                if body['summary'].endswith('Bad'): return Response(ConnectionError('offline'))
                return insert(calendarId=calendarId, body=body)
            resource.insert = fail_bad
            return resource
        with patch.object(main, 'calendar_service', return_value=google), patch.object(google, 'events', side_effect=resources):
            result = main.sync_to_google(self.session)
        self.session.refresh(good)
        self.session.refresh(bad)
        self.assertEqual((result['synced_count'], len(result['failed'])), (1, 1))
        self.assertEqual((good.status, bad.status), ('synced', 'scheduled'))

    def test_known_identity_404_reuses_same_id_and_protects_modifications(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1), google_event_id='abc123')
        with self.assertRaises(HTTPException):
            main.update_preferences(item.id, main.PreferencesRequest(time_mode='preferred'), self.session)
        with self.assertRaises(HTTPException):
            main.delete_task(item.id, self.session)
        google = FakeGoogle()
        with patch.object(main, 'calendar_service', return_value=google):
            result = main.sync_to_google(self.session)
        self.assertEqual(result['synced_count'], 1)
        self.assertEqual(google.insert_calls[0]['id'], 'abc123')
        self.session.refresh(item)
        self.assertEqual(item.google_event_id, 'abc123')

    def test_insert_conflict_recovers_matching_remote_event(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        google = FakeGoogle()
        original = google.events
        def resources():
            resource = original()
            def race_insert(calendarId, body):
                google.remote[body['id']] = body
                google.insert_calls.append(body)
                return Response(HttpError(type('R', (), {'status': 409, 'reason': 'Conflict'})(), b'{}'))
            resource.insert = race_insert
            return resource
        with patch.object(main, 'calendar_service', return_value=google), patch.object(google, 'events', side_effect=resources):
            result = main.sync_to_google(self.session)
        self.assertEqual(result['synced_count'], 1)
        self.assertEqual(result['failed'], [])
        self.session.refresh(item)
        self.assertEqual(item.status, 'synced')
    def test_legacy_migration_preserves_task_and_is_repeatable(self):
        legacy = create_engine('sqlite://')
        with legacy.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE task (id INTEGER PRIMARY KEY, title TEXT, energy_level INTEGER, duration_minutes INTEGER, target_date TEXT, preferred_time TEXT, priority INTEGER, location_context TEXT, estimated_transit_minutes INTEGER, status TEXT, start_time DATETIME, end_time DATETIME, preferred_start_time TEXT)')
            connection.exec_driver_sql("INSERT INTO task (id,title,duration_minutes,status) VALUES (7,'Existing task',60,'new')")
        with patch.object(main, 'engine', legacy):
            main.on_startup()
            main.on_startup()
        with legacy.connect() as connection:
            row = connection.exec_driver_sql('SELECT id,title,time_mode,locked FROM task').one()
        self.assertEqual(tuple(row), (7, 'Existing task', 'fixed', 0))
        legacy.dispose()

class EditingTests(unittest.TestCase):
    setUp = BackendTests.setUp
    tearDown = BackendTests.tearDown
    add = BackendTests.add

    def test_unchanged_preferences_preserve_locked_proposal(self):
        item = self.add(status='scheduled', locked=True, start_time=self.start,
                        end_time=self.start+timedelta(hours=1), time_mode='fixed',
                        preferred_start_time='15:00')
        main.update_preferences(item.id, main.PreferencesRequest(time_mode='fixed',
                                preferred_start_time='15:00'), self.session)
        self.session.refresh(item)
        self.assertEqual((item.status, item.start_time, item.locked), ('scheduled', self.start, True))

    def test_manual_edit_locks_and_updates_duration(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        with patch.object(main, 'calendar_service', return_value=FakeGoogle()):
            main.update_task_time(item.id, main.UpdateTaskTimeRequest(
                start_time=self.start+timedelta(hours=1),
                end_time=self.start+timedelta(hours=2, minutes=30)), self.session)
        self.session.refresh(item)
        self.assertTrue(item.locked)
        self.assertEqual(item.duration_minutes, 90)

    def test_conflicting_manual_edit_preserves_original_time(self):
        item = self.add(status='scheduled', start_time=self.start,
                        end_time=self.start+timedelta(hours=1))
        self.add(status='scheduled', locked=True, start_time=self.start+timedelta(hours=2),
                 end_time=self.start+timedelta(hours=3))
        with patch.object(main, 'calendar_service', return_value=FakeGoogle()):
            with self.assertRaises(HTTPException) as error:
                main.update_task_time(item.id, main.UpdateTaskTimeRequest(
                    start_time=self.start+timedelta(hours=2),
                    end_time=self.start+timedelta(hours=3)), self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.session.refresh(item)
        self.assertEqual(item.start_time, self.start)

class RouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        def session_override():
            with Session(self.engine) as session: yield session
        main.app.dependency_overrides[main.get_session] = session_override
        self.client = TestClient(main.app)
    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_create_and_list_serialize_validated_ai_output(self):
        import json
        from types import SimpleNamespace
        with patch.dict(main.os.environ, {'GEMINI_API_KEY': 'offline-test'}), patch.object(main.genai, 'GenerativeModel') as model:
            model.return_value.generate_content.return_value = SimpleNamespace(text=json.dumps({
                'title': 'Study', 'duration_minutes': 90, 'preferred_start_time': '15:00', 'time_mode': 'fixed'}))
            response = self.client.post('/smart_add_task', json={'text': 'Study at 15:00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task']['duration_minutes'], 90)
        listed = self.client.get('/tasks')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]['time_mode'], 'fixed')

    def test_invalid_requests_return_422(self):
        self.assertEqual(self.client.post('/smart_add_task', json={'text': '   '}).status_code, 422)
        self.assertEqual(self.client.put('/tasks/1/time', json={
            'start_time': '2026-01-01T15:00:00', 'end_time': '2026-01-01T14:00:00'}).status_code, 422)

    def test_route_returns_conflict_409(self):
        day = main.datetime.now(main.TZ).date() + timedelta(days=2)
        start = datetime.combine(day, time(15))
        with Session(self.engine) as session:
            item = main.Task(title='Move me', duration_minutes=60, status='scheduled',
                             start_time=start, end_time=start+timedelta(hours=1))
            blocker = main.Task(title='Locked', duration_minutes=60, status='scheduled', locked=True,
                                start_time=start+timedelta(hours=2), end_time=start+timedelta(hours=3))
            session.add_all([item, blocker])
            session.commit()
            session.refresh(item)
            task_id = item.id
        with patch.object(main, 'calendar_service', return_value=FakeGoogle()):
            response = self.client.put(f'/tasks/{task_id}/time', json={
                'start_time': (start+timedelta(hours=2)).isoformat(),
                'end_time': (start+timedelta(hours=3)).isoformat()})
        self.assertEqual(response.status_code, 409)
        self.assertIsInstance(response.json()['detail'], str)

    def test_schedule_and_sync_payload_shapes(self):
        with patch.object(main, 'calendar_service', return_value=FakeGoogle()):
            scheduled = self.client.post('/schedule_tasks')
            synced = self.client.post('/sync_to_google')
        self.assertEqual(scheduled.status_code, 200)
        self.assertEqual(scheduled.json()['unscheduled'], [])
        self.assertEqual(scheduled.json()['scheduled_count'], 0)
        self.assertEqual(synced.status_code, 200)
        self.assertEqual(synced.json()['failed'], [])
        self.assertEqual(synced.json()['synced_count'], 0)

if __name__ == '__main__': unittest.main()






