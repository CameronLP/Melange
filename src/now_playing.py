# now_playing.py
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

import time

from gi.repository import Gio, GLib

# The standard Linux desktop "what's playing" interface - Spotify,
# browsers, VLC, Rhythmbox etc. all implement this on the session bus.
# Melange itself only ever captures raw system audio (GStreamer) and
# has no other way to know a track's title/artist/artwork.
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


class NowPlayingWatcher:

    # on_change(info) is called with either None (nothing currently
    # playing anywhere) or a dict {"title", "artist", "art_url"} -
    # never with stale/paused info, so callers don't need to guess
    # whether what they're showing is still accurate.
    def __init__(self, on_change):

        self.on_change = on_change
        self.players = {}
        self.last_active = {}

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_name_owner_changed
        )

        self._discover_existing_players()

    def _discover_existing_players(self):

        try:
            bus_proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", None
            )

            result = bus_proxy.call_sync(
                "ListNames", None, Gio.DBusCallFlags.NONE, -1, None
            )

        except GLib.Error:
            return

        for name in result.unpack()[0]:
            if name.startswith(MPRIS_PREFIX):
                self._add_player(name)

    def _on_name_owner_changed(
        self, connection, sender, path, interface, signal, params
    ):

        name, old_owner, new_owner = params.unpack()

        if not name.startswith(MPRIS_PREFIX):
            return

        if new_owner and not old_owner:
            self._add_player(name)
        elif old_owner and not new_owner:
            self._remove_player(name)

    def _add_player(self, name):

        if name in self.players:
            return

        try:
            proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                name, MPRIS_PATH, MPRIS_PLAYER_IFACE, None
            )
        except GLib.Error:
            return

        proxy.connect("g-properties-changed", self._on_properties_changed, name)

        self.players[name] = proxy
        self.last_active[name] = time.monotonic()

        self._report_active()

    def _remove_player(self, name):

        self.players.pop(name, None)
        self.last_active.pop(name, None)

        self._report_active()

    # Only bumps last_active on an actual transition *into* Playing,
    # not on every properties-changed signal - a real player emits
    # those constantly for routine playback-position ticks unrelated
    # to the user's intent, which (confirmed against real running
    # players, not just a mock) otherwise keeps a chatty already-
    # playing player perpetually "more recent" than one that just
    # started, making the choice of which player to show effectively
    # arbitrary/flickery whenever more than one is Playing at once.
    def _on_properties_changed(self, proxy, changed, invalidated, name):

        if changed.unpack().get("PlaybackStatus") == "Playing":
            self.last_active[name] = time.monotonic()

        self._report_active()

    def _report_active(self):

        playing = [
            name for name, proxy in self.players.items()
            if self._playback_status(proxy) == "Playing"
        ]

        if not playing:
            self.on_change(None)
            return

        playing.sort(key=lambda n: self.last_active.get(n, 0), reverse=True)

        active = self.players[playing[0]]
        metadata_variant = active.get_cached_property("Metadata")
        metadata = metadata_variant.unpack() if metadata_variant else {}

        artists = metadata.get("xesam:artist", [])

        self.on_change({
            "title": metadata.get("xesam:title", ""),
            "artist": ", ".join(artists) if artists else "",
            "art_url": metadata.get("mpris:artUrl", "")
        })

    def _playback_status(self, proxy):

        status = proxy.get_cached_property("PlaybackStatus")

        return status.get_string() if status else None
