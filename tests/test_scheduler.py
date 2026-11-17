"""Offline scheduling regression tests; run: python -m unittest discover -s tests -v."""
import unittest
from datetime import date, datetime
from types import SimpleNamespace

from scheduler import choose_slot, free_blocks, overlaps, subtract, wall

DAY = date(2026, 11, 18)

def at(hour, minute=0, day=18):
    return datetime(2026, 11, day, hour, minute)

def task(**overrides):
    values = dict(duration_minutes=60, estimated_transit_minutes=0,
                  location_context='Anywhere', preferred_start_time=None,
                  preferred_time='Any', energy_level=3, time_mode='preferred')
    values.update(overrides)
    return SimpleNamespace(**values)

class SchedulingTests(unittest.TestCase):
    def test_split_retains_both_sides_of_late_placement(self):
        blocks = subtract([(at(8), at(22))], at(15), at(16))
        self.assertEqual(blocks, [(at(8), at(15)), (at(16), at(22))])
        self.assertEqual(choose_slot(task(), DAY, blocks)[1], at(8))

    def test_energy_falls_back_without_overwriting_user_preference(self):
        item = task(energy_level=3)
        slot = choose_slot(item, DAY, [(at(13), at(17))])
        self.assertEqual(slot[1:3], (at(13), at(14)))
        self.assertEqual(item.preferred_time, 'Any')

    def test_energy_prefers_morning_even_if_earlier_nonpreferred_block_exists(self):
        slot = choose_slot(task(energy_level=1), DAY,
                           [(at(8), at(12)), (at(17), at(22))])
        self.assertEqual(slot[1], at(17))

    def test_fixed_time_conflict_does_not_silently_move(self):
        slot = choose_slot(task(time_mode='fixed', preferred_start_time='15:00'),
                           DAY, [(at(8), at(14)), (at(16), at(22))])
        self.assertIsNone(slot)

    def test_preferred_time_can_move_to_nearest_available_slot(self):
        slot = choose_slot(task(time_mode='preferred', preferred_start_time='15:00'),
                           DAY, [(at(8), at(14)), (at(16), at(22))])
        self.assertEqual(slot[1], at(16))

    def test_explicit_morning_preference_remains_constraint(self):
        self.assertIsNone(choose_slot(task(preferred_time='Morning'), DAY,
                                      [(at(13), at(22))]))

    def test_all_day_busy_interval_blocks_entire_workday(self):
        blocks = free_blocks(DAY, [(at(0), at(0, day=19))], at(10, day=17))
        self.assertEqual(blocks, [])

    def test_overlapping_multiday_events_leave_no_false_gaps(self):
        blocks = free_blocks(DAY, [(at(20, day=17), at(11)), (at(10), at(14))],
                             at(10, day=17))
        self.assertEqual(blocks, [(at(14), at(22))])

    def test_today_excludes_past_and_ten_minute_buffer(self):
        self.assertEqual(free_blocks(DAY, [], at(14, 25)), [(at(14, 35), at(22))])
        self.assertEqual(free_blocks(DAY, [], at(21, 55)), [])

    def test_previous_days_cannot_be_scheduled(self):
        self.assertEqual(free_blocks(DAY, [], at(8, day=19)), [])

    def test_touching_events_are_not_overlaps(self):
        self.assertFalse(overlaps(at(8), at(9), at(9), at(10)))
        self.assertTrue(overlaps(at(8), at(9, 1), at(9), at(10)))

    def test_calendar_offsets_convert_instead_of_being_discarded(self):
        self.assertEqual(wall('2026-11-18T08:00:00Z'), at(10))
        self.assertEqual(wall('2026-07-18T08:00:00Z'), datetime(2026, 7, 18, 11))

    def test_transit_is_reserved_before_fixed_task(self):
        item = task(time_mode='fixed', preferred_start_time='09:00',
                    estimated_transit_minutes=30, location_context='Campus')
        slot = choose_slot(item, DAY, [(at(8), at(12))])
        self.assertEqual(slot[1:], (at(9), at(10), at(8, 30)))
        self.assertIsNone(choose_slot(item, DAY, [(at(8, 45), at(12))]))

if __name__ == '__main__':
    unittest.main()
