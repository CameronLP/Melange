import gi
import threading
import http.server
import socketserver
from pathlib import Path

gi.require_version("WebKit", "6.0")

from gi.repository import WebKit


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

    view = WebKit.WebView(
        user_content_manager=content_manager
    )

    # This is an embedded single-purpose visualizer, not a browser tab,
    # so there's no autoplay-abuse concern. Without this,
    # AudioContext.resume() only ever succeeds when called from JS that
    # runs as a direct consequence of a real DOM click - which never
    # happens on page load, so the system-audio analyser silently never
    # starts.
    view.get_settings().set_media_playback_requires_user_gesture(False)

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
