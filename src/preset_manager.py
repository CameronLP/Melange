# preset_manager.py
#
# Copyright 2026 Cameron
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Renderer-independent preset navigation: history, queue, shuffle, cycling.

This used to live in the Butterchurn page (main.js nextPreset/
previousPreset/goToPreset/scheduleCycleTick and the queue functions).
It's in Python now so the same behaviour drives every renderer - the
renderer only has to show a preset by name (see `show`).

Behaviour (unchanged from the JS version):
- next(): plays the queue first (front item; with Loop on it goes back
  to the end instead of being dropped). Otherwise, after going back,
  replays forward through history. Otherwise picks a new preset -
  random but never the current one with Shuffle on, else the next one
  in catalog order - and starts a new history branch from there.
- previous(): steps back through what was actually shown.
- Auto-cycle: every `cycle_interval` seconds (0 = off), +/- a random
  jitter fraction, unless paused (e.g. while the preset is locked).
"""

import random

from gi.repository import GLib


class PresetManager:

    def __init__(self, show, on_queue_changed=None, schedule=None, cancel=None, rng=None):
        # show(name) - make the renderer display this preset.
        self.show = show
        self.on_queue_changed = on_queue_changed

        # Injectable so tests can drive the cycle timer by hand.
        self.schedule = schedule or (
            lambda seconds, callback: GLib.timeout_add(int(seconds * 1000), callback)
        )
        self.cancel = cancel or GLib.source_remove
        self.rng = rng or random.Random()

        self.catalog = []
        self.catalog_set = set()

        self.current = None
        self.history = []
        self.history_position = -1

        self.shuffle = True
        self.queue = []
        self.queue_loop = False
        self.queue_shuffle = False

        self.cycle_interval = 0.0
        self.cycle_jitter = 0.0
        self.cycle_paused = False
        self.active = False
        self._cycle_source = None

    # --- catalog --------------------------------------------------------

    def set_catalog(self, names):

        self.catalog = list(names)
        self.catalog_set = set(self.catalog)

        # Drop queued names that no longer exist (e.g. a removed file).
        kept = [name for name in self.queue if name in self.catalog_set]

        if kept != self.queue:
            self.queue = kept
            self._queue_changed()

    def contains(self, name):

        return name in self.catalog_set

    def set_current_without_showing(self, name):
        """Record what the renderer is already showing (e.g. its own
        startup preset) as the start of history, without re-showing it."""

        self.current = name
        self.history = [name]
        self.history_position = 0

    # --- navigation -----------------------------------------------------

    def go_to(self, name, show=True):
        """Switch to `name` and start a new history branch there. With
        show=False it's only recorded - for when the renderer already
        switched by itself (e.g. Butterchurn after loading a file)."""

        if name not in self.catalog_set:
            return False

        self.current = name

        if show:
            self.show(name)

        # A new pick after going back discards the forward history,
        # like a browser tab following a link mid-back-navigation.
        del self.history[self.history_position + 1:]
        self.history.append(name)
        self.history_position = len(self.history) - 1

        return True

    def next(self):

        if self.queue:

            name = self.queue.pop(0)

            if self.queue_loop:
                self.queue.append(name)

            self._queue_changed()

            # Queue entries are always catalog names (enqueue/set_queue/
            # set_catalog filter), so this only falls through if the
            # catalog changed underneath.
            if self.go_to(name):
                return

        if self.history_position < len(self.history) - 1:
            self.history_position += 1
            self.current = self.history[self.history_position]
            self.show(self.current)
            return

        if not self.catalog:
            return

        if self.shuffle and len(self.catalog) > 1:
            while True:
                name = self.rng.choice(self.catalog)
                if name != self.current:
                    break
        else:
            index = self.catalog.index(self.current) if self.current in self.catalog_set else -1
            name = self.catalog[(index + 1) % len(self.catalog)]

        self.go_to(name)

    def previous(self):

        if self.history_position <= 0:
            return

        self.history_position -= 1
        self.current = self.history[self.history_position]
        self.show(self.current)

    def set_shuffle(self, enabled):

        self.shuffle = bool(enabled)

    # --- queue ----------------------------------------------------------

    def enqueue(self, name):

        if name not in self.catalog_set:
            return False

        self.queue.append(name)
        self._queue_changed()

        return True

    def set_queue(self, names):

        self.queue = [name for name in names if name in self.catalog_set]

        if self.queue_shuffle:
            self.rng.shuffle(self.queue)

        self._queue_changed()

    def remove_queue_item(self, index):

        if 0 <= index < len(self.queue):
            del self.queue[index]
            self._queue_changed()

    def move_queue_item(self, index, delta):

        self.move_queue_item_to(index, index + delta)

    def move_queue_item_to(self, from_index, to_index):

        count = len(self.queue)

        if (
            not 0 <= from_index < count
            or not 0 <= to_index < count
            or from_index == to_index
        ):
            return

        self.queue.insert(to_index, self.queue.pop(from_index))
        self._queue_changed()

    def set_queue_loop(self, enabled):

        self.queue_loop = bool(enabled)

    def set_queue_shuffle(self, enabled):

        self.queue_shuffle = bool(enabled)

        if self.queue_shuffle:
            self.rng.shuffle(self.queue)
            self._queue_changed()

    def _queue_changed(self):

        if self.on_queue_changed:
            self.on_queue_changed(list(self.queue))

    # --- auto-cycle -----------------------------------------------------

    def set_active(self, active):
        """Only the active renderer's manager runs its cycle timer."""

        self.active = bool(active)
        self._reschedule()

    def set_cycle_interval(self, seconds):

        self.cycle_interval = max(0.0, float(seconds))
        self._reschedule()

    def set_cycle_jitter(self, percent):

        self.cycle_jitter = max(0.0, float(percent)) / 100

    def set_cycle_paused(self, paused):

        self.cycle_paused = bool(paused)
        self._reschedule()

    def _reschedule(self):

        if self._cycle_source is not None:
            self.cancel(self._cycle_source)
            self._cycle_source = None

        if self.active and not self.cycle_paused and self.cycle_interval > 0:
            factor = 1 + (self.rng.random() * 2 - 1) * self.cycle_jitter
            self._cycle_source = self.schedule(
                self.cycle_interval * factor, self._on_cycle
            )

    def _on_cycle(self):

        self._cycle_source = None
        self.next()
        self._reschedule()

        return False
