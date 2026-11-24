"""Tests for src/preset_manager.py.

Run from the repository root:
    python3 -m unittest discover tests
(needs PyGObject, which the module imports for its default GLib timer.)
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from preset_manager import PresetManager  # noqa: E402


class FakeTimers:
    """Stands in for GLib.timeout_add/source_remove."""

    def __init__(self):
        self.pending = {}
        self.next_id = 1

    def schedule(self, seconds, callback):
        source = self.next_id
        self.next_id += 1
        self.pending[source] = (seconds, callback)
        return source

    def cancel(self, source):
        del self.pending[source]

    def fire(self):
        (source, (seconds, callback)), = self.pending.items()
        del self.pending[source]
        callback()
        return seconds


def make(catalog=("a", "b", "c", "d"), shuffle=False, seed=0):
    shown, queues = [], []
    timers = FakeTimers()
    manager = PresetManager(
        show=shown.append,
        on_queue_changed=queues.append,
        schedule=timers.schedule,
        cancel=timers.cancel,
        rng=random.Random(seed),
    )
    manager.set_catalog(catalog)
    manager.set_shuffle(shuffle)
    return manager, shown, queues, timers


class NavigationTests(unittest.TestCase):

    def test_sequential_next_wraps(self):
        m, shown, _, _ = make()
        m.set_current_without_showing("c")
        m.next()
        m.next()
        self.assertEqual(shown, ["d", "a"])

    def test_next_from_nothing_starts_at_first(self):
        m, shown, _, _ = make()
        m.next()
        self.assertEqual(shown, ["a"])

    def test_previous_retraces_and_next_replays(self):
        m, shown, _, _ = make()
        m.set_current_without_showing("a")
        m.next()      # b
        m.next()      # c
        m.previous()  # b
        m.previous()  # a
        m.previous()  # at start: nothing
        m.next()      # replays b
        m.next()      # replays c
        m.next()      # new: d
        self.assertEqual(shown, ["b", "c", "b", "a", "b", "c", "d"])

    def test_new_pick_after_back_discards_forward_history(self):
        m, shown, _, _ = make()
        m.set_current_without_showing("a")
        m.next()          # b
        m.next()          # c
        m.previous()      # b
        m.go_to("d")      # branch off
        m.next()          # no forward history left: sequential after d
        self.assertEqual(m.history, ["a", "b", "d", "a"])
        self.assertEqual(shown[-2:], ["d", "a"])

    def test_shuffle_never_repeats_current(self):
        m, shown, _, _ = make(shuffle=True)
        m.set_current_without_showing("a")
        previous = "a"
        for _ in range(200):
            m.next()
            self.assertNotEqual(shown[-1], previous)
            previous = shown[-1]
        self.assertEqual(set(shown), {"a", "b", "c", "d"})

    def test_shuffle_with_single_preset_falls_back_to_sequential(self):
        m, shown, _, _ = make(catalog=("only",), shuffle=True)
        m.set_current_without_showing("only")
        m.next()
        self.assertEqual(shown, ["only"])

    def test_go_to_without_showing_records_history(self):
        m, shown, _, _ = make()
        m.set_current_without_showing("a")
        m.go_to("c", show=False)
        m.previous()
        self.assertEqual(shown, ["a"])
        self.assertEqual(m.history, ["a", "c"])

    def test_go_to_unknown_is_ignored(self):
        m, shown, _, _ = make()
        self.assertFalse(m.go_to("nope"))
        self.assertEqual(shown, [])

    def test_empty_catalog_does_nothing(self):
        m, shown, _, _ = make(catalog=())
        m.next()
        m.previous()
        self.assertEqual(shown, [])


class QueueTests(unittest.TestCase):

    def test_queue_plays_first_and_drains(self):
        m, shown, queues, _ = make()
        m.set_current_without_showing("a")
        m.enqueue("d")
        m.enqueue("c")
        m.next()
        m.next()
        m.next()  # queue empty: sequential after c
        self.assertEqual(shown, ["d", "c", "d"])
        self.assertEqual(queues[-1], [])

    def test_queue_loop_rotates(self):
        m, shown, queues, _ = make()
        m.set_queue_loop(True)
        m.set_queue(["c", "a"])
        m.next()
        m.next()
        m.next()
        self.assertEqual(shown, ["c", "a", "c"])
        self.assertEqual(queues[-1], ["a", "c"])

    def test_set_queue_filters_unknown_and_enqueue_rejects_unknown(self):
        m, _, queues, _ = make()
        m.set_queue(["a", "zz", "b"])
        self.assertFalse(m.enqueue("zz"))
        self.assertEqual(queues[-1], ["a", "b"])

    def test_queue_shuffle_keeps_items(self):
        m, _, queues, _ = make(catalog=list("abcdefgh"))
        m.set_queue(list("abcdefgh"))
        m.set_queue_shuffle(True)
        self.assertEqual(sorted(queues[-1]), list("abcdefgh"))
        self.assertNotEqual(queues[-1], list("abcdefgh"))

    def test_move_and_remove(self):
        m, _, queues, _ = make()
        m.set_queue(["a", "b", "c"])
        m.move_queue_item(0, 1)
        self.assertEqual(queues[-1], ["b", "a", "c"])
        m.move_queue_item_to(2, 0)
        self.assertEqual(queues[-1], ["c", "b", "a"])
        m.remove_queue_item(1)
        self.assertEqual(queues[-1], ["c", "a"])
        count = len(queues)
        m.move_queue_item(0, -1)      # out of range: no change
        m.remove_queue_item(5)
        self.assertEqual(len(queues), count)

    def test_catalog_change_drops_missing_queue_items(self):
        m, _, queues, _ = make()
        m.set_queue(["a", "d"])
        m.set_catalog(["a", "b"])
        self.assertEqual(queues[-1], ["a"])


class CycleTests(unittest.TestCase):

    def test_cycles_only_when_active_and_unpaused(self):
        m, shown, _, timers = make()
        m.set_current_without_showing("a")
        m.set_cycle_interval(30)
        self.assertEqual(timers.pending, {})        # not active yet
        m.set_active(True)
        self.assertEqual(timers.fire(), 30)
        self.assertEqual(shown, ["b"])
        self.assertEqual(len(timers.pending), 1)    # rescheduled
        m.set_cycle_paused(True)
        self.assertEqual(timers.pending, {})
        m.set_cycle_paused(False)
        m.set_active(False)
        self.assertEqual(timers.pending, {})

    def test_interval_zero_turns_cycling_off(self):
        m, _, _, timers = make()
        m.set_active(True)
        m.set_cycle_interval(10)
        m.set_cycle_interval(0)
        self.assertEqual(timers.pending, {})

    def test_jitter_stays_within_range(self):
        m, _, _, timers = make()
        m.set_cycle_jitter(20)
        m.set_cycle_interval(10)
        m.set_active(True)
        delays = [timers.fire() for _ in range(200)]
        self.assertTrue(all(8.0 <= d <= 12.0 for d in delays))
        self.assertGreater(max(delays) - min(delays), 2.0)


if __name__ == "__main__":
    unittest.main()
