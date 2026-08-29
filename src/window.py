import gi
import json
import subprocess
import threading
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

        # Webview

        self.webview = create_webview(
            on_message=self.on_webview_debug_message
        )

        self.content_box.append(
            self.webview
        )

        self.gst_pipeline = None
        self.current_sink = None

        self.start_system_audio()

        threading.Thread(
            target=self.watch_audio_changes,
            daemon=True
        ).start()

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
            GLib.Variant("s", "system")
        )

        action.connect(
            "change-state",
            self.audio_source_changed
        )

        self.add_action(action)



    def on_webview_debug_message(self, text):

        if text != "APP_READY":
            return

        # Setting the action's initial state in new_stateful() only
        # sets its internal value - it does not fire "change-state"
        # (that only happens on a real activation, e.g. clicking the
        # menu item), so without this the webview never actually gets
        # told to use system audio until the user opens the menu
        # themselves. "APP_READY" is sent by main.js once
        # window.setAudioSource is actually defined and safe to call.
        action = self.lookup_action("audio-source")

        action.change_state(
            GLib.Variant("s", "system")
        )


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


    # System Audio Capture (Pipewire)

    def start_system_audio(self):

        sink_name = self.get_default_sink()

        self.current_sink = sink_name

        print("Capturing monitor of sink:", sink_name)

        # target-object must be the sink's own node name, not its
        # ".monitor" name (pipewiresrc only matches real PipeWire node
        # names/serials, and the deprecated `path` property only takes
        # numeric ids). stream.capture.sink=true is what actually taps
        # the sink's monitor ports instead of opening a real capture
        # device - without it, WirePlumber may treat this as a
        # microphone request and, on Bluetooth sinks, drop the a2dp
        # connection to the lower quality headset profile.
        self.gst_pipeline = Gst.parse_launch(
            f"""
            pipewiresrc
            target-object="{sink_name}"
            stream-properties="props,stream.capture.sink=true"
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
            buffer = sample.get_buffer()

            ok, mapinfo = buffer.map(
                Gst.MapFlags.READ
            )

            if ok:
                data = bytes(mapinfo.data)
                buffer.unmap(mapinfo)

                GLib.idle_add(
                    self.send_audio_to_webview,
                    data
                )

        return Gst.FlowReturn.OK



    def send_audio_to_webview(self, data):

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



    def get_default_sink(self):

        return subprocess.check_output(
            ["pactl", "get-default-sink"]
        ).decode().strip()

    def watch_audio_changes(self):

        process = subprocess.Popen(
            ["pactl", "subscribe"],
            stdout=subprocess.PIPE,
            text=True
        )

        for line in process.stdout:

            # Default sink/source changes are reported as a change on
            # the server object, not on a specific sink - filtering on
            # "sink" here would also fire on unrelated per-app volume
            # changes (sink-input events) and restart the pipeline for
            # no reason.
            if "on server" in line:

                GLib.idle_add(
                    self.check_default_sink_changed
                )

    def check_default_sink_changed(self):

        sink_name = self.get_default_sink()

        if sink_name != self.current_sink:

            print("Default sink changed:", self.current_sink, "->", sink_name)

            self.restart_audio_monitor()

        return False

    def restart_audio_monitor(self):

        if self.gst_pipeline:

            self.gst_pipeline.set_state(
                Gst.State.NULL
            )

        self.start_system_audio()

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
