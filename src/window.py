import gi
import json
import subprocess
import threading
import math
import struct
import base64

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Gst
from melange.webview import create_webview

Gst.init(None)

@Gtk.Template(resource_path="/com/cameronlp/Melange/window.ui")
class MelangeWindow(Adw.ApplicationWindow):

    __gtype_name__ = "MelangeWindow"

    content_box = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    headerbar = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()

    next_button = Gtk.Template.Child()
    previous_button = Gtk.Template.Child()

    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        self.menu_open = False
        self.hide_timer = None
        self.mouse_over_toolbar = False

        self.toolbar_view.set_extend_content_to_top_edge(True)
        self.headerbar.add_css_class("melange-header")

        self.get_monitor_source()

        # Webview

        self.webview = create_webview()

        #threading.Thread(
        #    target=self.start_audio_monitor,
        #    args=(self.webview,),
        #    daemon=True
        #).start()

        #threading.Thread(
        #    target=self.watch_audio_changes,
        #    daemon=True
        #).start()

        self.content_box.append(
            self.webview
        )

        self.gst_pipeline = None

        self.start_system_audio()

        print(
            subprocess.check_output(
                ["pactl", "list", "sources", "short"]
            ).decode()
        )




        # Mouse and Toolbar

        motion = Gtk.EventControllerMotion()

        motion.connect(
            "motion",
            self.mouse_move
        )

        toolbar_motion = Gtk.EventControllerMotion()

        toolbar_motion.connect(
            "enter",
            self.toolbar_enter
        )

        toolbar_motion.connect(
            "leave",
            self.toolbar_leave
        )

        self.toolbar_view.add_controller(
            toolbar_motion
        )

        self.add_controller(motion)





        # Toolbar Buttons

        self.menu_button.connect(
            "notify::active",
            self.menu_changed
        )

        self.next_button.connect(
            "clicked",
            self.next_preset
        )

        self.previous_button.connect(
            "clicked",
            self.previous_preset
        )



        # Audio
        action = Gio.SimpleAction.new_stateful(
            "audio-source",
            GLib.VariantType.new("s"),
            GLib.Variant("s", "none")
        )

        action.connect(
            "change-state",
            self.audio_source_changed
        )

        self.add_action(action)



    def toolbar_enter(self, controller, x, y):

        self.mouse_over_toolbar = True

        if self.hide_timer:

            GLib.source_remove(
                self.hide_timer
            )

            self.hide_timer = None

        self.toolbar_view.set_reveal_top_bars(True)



    def toolbar_leave(self, controller):

        self.mouse_over_toolbar = False

        self.hide_timer = GLib.timeout_add_seconds(
            3,
            self.hide_toolbar
        )


    def mouse_move(self, controller, x, y):

        self.toolbar_view.set_reveal_top_bars(True)

        GLib.timeout_add_seconds(
            3,
            self.hide_toolbar
        )


    def hide_toolbar(self):

        self.hide_timer = None

        if self.mouse_over_toolbar:
            return False

        if self.menu_open:
            return False

        self.toolbar_view.set_reveal_top_bars(False)

        return False

    def menu_changed(self, button, param):

        self.menu_open = button.get_active()

        if self.menu_open:
            self.toolbar_view.set_reveal_top_bars(True)












    # Callbacks


    def next_preset(self, button):

        self.run_js("nextPreset();")

        #self.webview.evaluate_javascript(
        #    "nextPreset();",
        #    -1,
        #    None,
        #    None,
        #    None,
        #    None
        #)

    def previous_preset(self, button):

        self.run_js("previousPreset();")

        #self.webview.evaluate_javascript(
        #    "previousPreset();",
        #    -1,
        #    None,
        #    None,
        #    None,
        #    None
        #)



    def audio_source_changed(self, action, value):

        source = value.get_string()

        print("Audio source (Python):", source)

        action.set_state(value)

        self.run_js("setAudioSource(" + json.dumps(source) + ")")

        #self.webview.evaluate_javascript(
        #    "setAudioSource(" + json.dumps(source) + ")",
        #    -1,
        #    None,
        #    None,
        #    None,
        #    None
        #)


    def start_audio_monitor(self, webview):

        sink = subprocess.check_output(
            ["pactl", "get-default-sink"]
        ).decode().strip()

        monitor = sink + ".monitor"

        print("Starting capture:", monitor)


        cmd = [
            "parec",
            "--format=s16le",
            "--rate=48000",
            "--channels=2",
            "--device=" + monitor
        ]


        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE
        )







    # System Audio Capture (Pipewire)

    def start_system_audio(self):


        monitor = self.get_monitor_source()

        print("GStreamer monitor:", monitor)

        self.gst_pipeline = Gst.parse_launch(
            f"""
            pipewiresrc
            path={monitor}
            !
            audioconvert
            !
            audio/x-raw,format=S16LE,rate=44100,channels=2
            !
            appsink name=sink emit-signals=true sync=false
            """
        )


        self.gst_pipeline = Gst.parse_launch(
            """
            pipewiresrc
            path=bluez_output.C4_16_88_3B_02_E7.1.monitor
            !
            audio/x-raw,format=S16LE,rate=44100,channels=2
            !
            audioconvert
            !
            audioresample
            !
            appsink name=sink emit-signals=true sync=false
            """
        )



        sink = self.gst_pipeline.get_by_name("sink")

        sink.connect(
            "new-sample",
            self.on_audio_sample
        )

        self.gst_pipeline.set_state(
            Gst.State.PLAYING
        )


    def on_audio_sample(self, sink):

        sample = sink.emit("pull-sample")

        if sample:
            print(
                "CAPS:",
                sample.get_caps().to_string()
            )


        if sample:
            buffer = sample.get_buffer()

            ok, mapinfo = buffer.map(
                Gst.MapFlags.READ
            )

            if ok:
                data = bytes(mapinfo.data)
                buffer.unmap(mapinfo)

                # s16le debug
                samples = struct.unpack(
                    "<" + "h"*(len(data)//2),
                    data
                )

                peak = max(abs(x) for x in samples)

                avg = sum(abs(x) for x in samples) / len(samples)

                print(
                    "PCM:",
                    "peak=", peak,
                    "avg=", avg,
                    "first=", samples[:10]
                )

                print("PCM PEAK:", peak)

                GLib.idle_add(
                    self.send_audio_to_webview,
                    data
                )

        return Gst.FlowReturn.OK



    def send_audio_to_webview(self, data):

        import base64

        encoded = base64.b64encode(
            data
        ).decode("ascii")


        self.webview.evaluate_javascript(
            f"receiveAudio('{encoded}')",
            -1,
            None,
            None,
            None,
            None
        )

        return False



    def get_monitor_source(self):

        sink = subprocess.check_output(
            ["pactl", "get-default-sink"]
        ).decode().strip()

        print("DEFAULT SINK:", sink)

        monitor = sink + ".monitor"

        print("MONITOR:", monitor)

        return monitor

    def watch_audio_changes(self):

        process = subprocess.Popen(
            ["pactl", "subscribe"],
            stdout=subprocess.PIPE,
            text=True
        )

        for line in process.stdout:

            print("PULSE EVENT:", line.strip())

            if "sink" in line:

                GLib.idle_add(
                    self.restart_audio_monitor
                )



    def restart_audio_monitor(self):

        print("Restarting audio monitor")

        threading.Thread(
            target=self.start_audio_monitor,
            args=(self.webview,),
            daemon=True
        ).start()

        return False


    def run_js(self, script):

        GLib.idle_add(
            self.webview.evaluate_javascript,
            script,
            -1,
            None,
            None,
            None,
            None
        )
