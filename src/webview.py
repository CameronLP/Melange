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


def create_webview():

    view = WebKit.WebView()

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
