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
MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"

ACTIVE_STATUSES = ("Playing", "Paused")


class NowPlayingWatcher:

    # on_change(info) is called with either None (nothing Playing or
    # Paused anywhere) or a dict {"bus_name", "title", "artist",
    # "art_url", "status", "length_us"}.
    #
    # on_players_changed(identities), if given, is called whenever the
    # set of known players changes - identities is {bus_name: display
    # name}, letting a UI build a "pick a source" list without having
    # to poll for it.
    def __init__(self, on_change, on_players_changed=None):

        self.on_change = on_change
        self.on_players_changed = on_players_changed

        self.players = {}
        self.identities = {}
        self.last_active = {}
        self.preferred_source = None

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

    # None means "Auto" (the default best-guess heuristic below).
    # Falls straight back to auto, rather than showing nothing, if the
    # chosen source isn't currently known - covers both picking a
    # source that's since closed and a stale choice restored before
    # its player has (re)appeared on the bus.
    def set_preferred_source(self, bus_name):

        self.preferred_source = bus_name if bus_name in self.players else None
        self._report_active()

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
        self.identities[name] = self._fetch_identity(name)
        self.last_active[name] = time.monotonic()

        self._notify_players_changed()
        self._report_active()

    # org.mpris.MediaPlayer2.Identity (the *root* MPRIS interface, not
    # .Player) is the player's own human-readable name ("Firefox",
    # "Feishin", ...) - every real implementation exposes it, but this
    # still falls back to a name derived from the bus name itself
    # (stripping the well-known prefix and any trailing
    # ".instanceN"-style suffix some players append) so a source
    # selector never has to show a raw/blank entry.
    def _fetch_identity(self, name):

        try:
            root_proxy = Gio.DBusProxy.new_sync(
                self.bus, Gio.DBusProxyFlags.NONE, None,
                name, MPRIS_PATH, MPRIS_ROOT_IFACE, None
            )

            identity = root_proxy.get_cached_property("Identity")

            if identity:
                return identity.get_string()

        except GLib.Error:
            pass

        short = name[len(MPRIS_PREFIX):].split(".instance")[0]

        return short or name

    def _remove_player(self, name):

        self.players.pop(name, None)
        self.identities.pop(name, None)
        self.last_active.pop(name, None)

        if self.preferred_source == name:
            self.preferred_source = None

        self._notify_players_changed()
        self._report_active()

    def _notify_players_changed(self):

        if self.on_players_changed:
            self.on_players_changed(dict(self.identities))

    # Only bumps last_active on an actual transition *into* an active
    # status, not on every properties-changed signal - a real player
    # emits those constantly for routine playback-position ticks
    # unrelated to the user's intent, which (confirmed against real
    # running players, not just a mock) otherwise keeps a chatty
    # already-active player perpetually "more recent" than one that
    # just started, making the choice of which player to show
    # effectively arbitrary/flickery whenever more than one is active
    # at once.
    def _on_properties_changed(self, proxy, changed, invalidated, name):

        if changed.unpack().get("PlaybackStatus") in ACTIVE_STATUSES:
            self.last_active[name] = time.monotonic()

        self._report_active()

    def _report_active(self):

        if self.preferred_source:

            proxy = self.players.get(self.preferred_source)

            if proxy and self._playback_status(proxy) in ACTIVE_STATUSES:
                self.on_change(self._info_from_proxy(self.preferred_source, proxy))
                return

            self.on_change(None)
            return

        active = [
            name for name, proxy in self.players.items()
            if self._playback_status(proxy) in ACTIVE_STATUSES
        ]

        if not active:
            self.on_change(None)
            return

        # Playing outranks Paused outright; within the same tier,
        # whichever most recently transitioned into that tier wins.
        active.sort(
            key=lambda n: (
                self._playback_status(self.players[n]) == "Playing",
                self.last_active.get(n, 0)
            ),
            reverse=True
        )

        self.on_change(self._info_from_proxy(active[0], self.players[active[0]]))

    def _info_from_proxy(self, bus_name, proxy):

        metadata_variant = proxy.get_cached_property("Metadata")
        metadata = metadata_variant.unpack() if metadata_variant else {}

        artists = metadata.get("xesam:artist", [])

        return {
            "bus_name": bus_name,
            "title": metadata.get("xesam:title", ""),
            "artist": ", ".join(artists) if artists else "",
            "album": metadata.get("xesam:album", ""),
            "art_url": metadata.get("mpris:artUrl", ""),
            "status": self._playback_status(proxy),
            "length_us": metadata.get("mpris:length", 0),
        }

    def _playback_status(self, proxy):

        status = proxy.get_cached_property("PlaybackStatus")

        return status.get_string() if status else None

    # Position (playback progress, microseconds) is explicitly *not*
    # meant to be relied on via PropertiesChanged per the MPRIS spec -
    # players only signal on a real seek, not continuously as it
    # advances - so this is a one-off synchronous Properties.Get,
    # meant to be called by a UI on its own timer rather than cached
    # here. Returns None if the player doesn't support/expose it.
    def get_position_us(self, bus_name):

        proxy = self.players.get(bus_name)

        if not proxy:
            return None

        try:
            result = self.bus.call_sync(
                bus_name, MPRIS_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (MPRIS_PLAYER_IFACE, "Position")),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE, -1, None
            )

            return result.unpack()[0]

        except GLib.Error:
            return None
