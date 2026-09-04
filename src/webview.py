# webview.py
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

import gi
import threading
import http.server
import socketserver
from pathlib import Path

gi.require_version("WebKit", "6.0")

from gi.repository import WebKit, Gdk


def permission_request(webview, request):

    print("Permission request:", request)

    request.allow()

    return True


def start_server():

    web_dir = (
        Path(__file__).parent
        / "web"
        / "dist"
    )

    handler = http.server.SimpleHTTPRequestHandler

    import os
    os.chdir(web_dir)

    with socketserver.TCPServer(
        ("127.0.0.1", 0),
        handler
    ) as httpd:

        port = httpd.server_address[1]

        global SERVER_PORT
        SERVER_PORT = port

        httpd.serve_forever()


threading.Thread(
    target=start_server,
    daemon=True
).start()


def create_webview(on_message=None):

    def handle_debug_message(manager, js_value):

        text = js_value.to_string()

        print("[JS]", text)

        if on_message:
            on_message(text)

    content_manager = WebKit.UserContentManager()

    content_manager.register_script_message_handler("debug")

    content_manager.connect(
        "script-message-received::debug",
        handle_debug_message
    )

    # This page only ever talks to its own localhost server, is never
    # navigated away from, and has nothing worth remembering between
    # runs (no logins, no history, no user-entered data) - so there's
    # no reason to pay for WebKit's normal on-disk cookie/HTTP-cache/
    # IndexedDB backing stores, which persist by default. An ephemeral
    # session skips creating any of that.
    view = WebKit.WebView(
        user_content_manager=content_manager,
        network_session=WebKit.NetworkSession.new_ephemeral()
    )

    # WebKit otherwise paints its own opaque backing color wherever the
    # page itself hasn't painted yet (e.g. before first frame) rather
    # than deferring to whatever's behind the widget in the GTK render
    # tree - matters for window.py's Transparency Mode, where the
    # ancestor chain above this widget is made alpha-capable so the
    # real desktop can show through a faded window.
    view.set_background_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=0))

    # WebKit otherwise shows its own native right-click menu (Back/
    # Forward/Reload/Inspect Element - browser-tab items that make no
    # sense on a music visualizer). Returning True from this signal
    # tells WebKit the menu request was already handled, suppressing
    # its default popup. Left undiscovered until it turned out to be
    # exactly what was silently absorbing Hidden Mode's own right-
    # click escape hatch (window.py on_content_right_click) - WebKit
    # takes its own pointer grab to show that menu, which was
    # intercepting the click before it ever reached the CAPTURE-phase
    # gesture on content_box, and very likely explains the reported
    # "can't drag either" symptom too (a still-open native context
    # menu holding a grab blocks whatever's clicked next until it's
    # dismissed).
    view.connect("context-menu", lambda *args: True)

    settings = view.get_settings()

    # This is an embedded single-purpose visualizer, not a browser tab,
    # so there's no autoplay-abuse concern. Without this,
    # AudioContext.resume() only ever succeeds when called from JS that
    # runs as a direct consequence of a real DOM click - which never
    # happens on page load, so the system-audio analyser silently never
    # starts.
    settings.set_media_playback_requires_user_gesture(False)

    # Disabling browser-tab features this single static page never
    # uses - no real navigation (presets are switched entirely by JS,
    # not URL changes, so there's nothing to page-cache or go
    # back/forward through), no persistent storage (see the ephemeral
    # session above - html5_database/local_storage would otherwise
    # still allocate in-memory backing even without disk persistence),
    # no <video>/MediaSource playback, no links, no editable text.
    # getUserMedia (media_stream, for the microphone input mode) and
    # the WebAudio/WebGL pipeline Butterchurn actually runs on are
    # left untouched.
    settings.set_enable_html5_database(False)
    settings.set_enable_html5_local_storage(False)
    settings.set_enable_page_cache(False)
    settings.set_enable_mediasource(False)
    settings.set_enable_media_capabilities(False)
    settings.set_enable_fullscreen(False)
    settings.set_enable_resizable_text_areas(False)
    settings.set_enable_tabs_to_links(False)
    settings.set_enable_smooth_scrolling(False)
    settings.set_enable_site_specific_quirks(False)

    view.set_hexpand(True)
    view.set_vexpand(True)

    view.connect(
        "permission-request",
        permission_request
    )

    view.load_uri(
        f"http://127.0.0.1:{SERVER_PORT}/"
    )

    return view

def permission_request(webview, request):

    print("Permission request:", request)

    request.allow()

    print("Permission allowed")

    return True
