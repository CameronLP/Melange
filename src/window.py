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
    toast_overlay = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    headerbar = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()


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

        # Lets the window be dragged from anywhere, not just the
        # header bar. Has to run in the CAPTURE phase and on
        # content_box (the webview's parent) rather than the webview
        # itself - WebKit claims button presses for its own hit
        # testing, so a normal (bubble-phase) gesture on the webview
        # would never see them.
        drag_gesture = Gtk.GestureClick()

        drag_gesture.set_button(Gdk.BUTTON_PRIMARY)
        drag_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        drag_gesture.connect(
            "pressed",
            self.on_window_drag_pressed
        )

        self.content_box.add_controller(drag_gesture)

        self.gst_pipeline = None
        self.current_sink = None
        self.pinned_sink = None
        self.pactl_event_timer = None
        self.webview_ready = False
        self.preset_locked = False
        self.preset_names = []
        self.preset_list_store = None
        self.preset_browser_dialog = None

        self.start_system_audio()

        # The "Audio Source" submenu is defined empty in window.ui and
        # populated here since the list of real output devices changes
        # at runtime as things get plugged/unplugged.
        section0 = self.menu_button.get_menu_model().get_item_link(
            0, Gio.MENU_LINK_SECTION
        )

        self.audio_source_submenu = section0.get_item_link(
            0, Gio.MENU_LINK_SUBMENU
        )

        self.rebuild_audio_source_menu()

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

        self.build_sensitivity_control()



        # Audio
        action = Gio.SimpleAction.new_stateful(
            "audio-source",
            GLib.VariantType.new("s"),
            GLib.Variant("s", self.current_sink)
        )

        action.connect(
            "change-state",
            self.audio_source_changed
        )

        self.add_action(action)

        # Preset/fullscreen controls, exposed as actions (rather than
        # just calling run_js straight from the button handlers) so
        # they get real keyboard accelerators via
        # app.set_accels_for_action() in main.py, and so those
        # accelerators show up automatically in the shortcuts dialog
        # via action-name.
        next_action = Gio.SimpleAction.new("next-preset", None)

        next_action.connect(
            "activate",
            lambda action, param: self.next_preset(None)
        )

        self.add_action(next_action)

        previous_action = Gio.SimpleAction.new("previous-preset", None)

        previous_action.connect(
            "activate",
            lambda action, param: self.previous_preset(None)
        )

        self.add_action(previous_action)

        fullscreen_action = Gio.SimpleAction.new("toggle-fullscreen", None)

        fullscreen_action.connect(
            "activate",
            self.toggle_fullscreen
        )

        self.add_action(fullscreen_action)

        load_preset_action = Gio.SimpleAction.new("load-preset", None)

        load_preset_action.connect(
            "activate",
            self.load_preset_clicked
        )

        self.add_action(load_preset_action)

        browse_presets_action = Gio.SimpleAction.new("browse-presets", None)

        browse_presets_action.connect(
            "activate",
            self.browse_presets_clicked
        )

        self.add_action(browse_presets_action)

        lock_preset_action = Gio.SimpleAction.new_stateful(
            "lock-preset",
            None,
            GLib.Variant("b", False)
        )

        lock_preset_action.connect(
            "change-state",
            self.lock_preset_changed
        )

        self.add_action(lock_preset_action)



    def on_webview_debug_message(self, text):

        if text.startswith("PRESET_NAME:"):
            preset_name = text[len("PRESET_NAME:"):]
            self.set_title(f'Melange - "{preset_name}"')
            return

        # A failed Load Preset (bad MilkDrop conversion, invalid
        # Butterchurn JSON, etc.) otherwise has no user-facing
        # feedback at all - it only ever reached the debug log, so a
        # rejected load just silently looked like nothing happened.
        if text.startswith("LOAD_PRESET_ERROR:"):
            reason = text[len("LOAD_PRESET_ERROR:"):]
            self.show_toast(f"Couldn't load preset: {reason}")
            return

        # The actual preset names only exist in JS (from
        # butterchurn-presets, plus anything loaded via
        # win.load-preset) - this is Python's copy, used to build the
        # native preset browser list. Sent whenever the list changes.
        if text.startswith("PRESET_LIST:"):
            self.preset_names = json.loads(text[len("PRESET_LIST:"):])

            if self.preset_list_store is not None:
                self.preset_list_store.splice(
                    0,
                    self.preset_list_store.get_n_items(),
                    self.preset_names
                )

            return

        # Sent by the on-canvas nav arrows (index.html/main.js) instead
        # of calling nextPreset()/previousPreset() directly, so the
        # lock check (and the native toast it shows) lives in one
        # place regardless of whether a change was requested via those
        # arrows or the win.next-preset/win.previous-preset keyboard
        # shortcuts.
        if text == "NAV_NEXT":
            self.next_preset(None)
            return

        if text == "NAV_PREVIOUS":
            self.previous_preset(None)
            return

        if text != "APP_READY":
            return

        self.webview_ready = True

        # Setting the action's initial state in new_stateful() only
        # sets its internal value - it does not fire "change-state"
        # (that only happens on a real activation, e.g. clicking the
        # menu item), so without this the webview never actually gets
        # told to use system audio until the user opens the menu
        # themselves. "APP_READY" is sent by main.js once
        # window.setAudioSource is actually defined and safe to call.
        action = self.lookup_action("audio-source")

        action.change_state(
            GLib.Variant("s", self.current_sink)
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


    def on_window_drag_pressed(self, gesture, n_press, x, y):

        widget = gesture.get_widget()

        ok, bounds = widget.compute_bounds(self)

        if not ok:
            return

        # Skip the leftmost/rightmost 15% - that's where the on-canvas
        # preset nav arrows live (see index.html's .nav-zone rule).
        # This gesture runs in the CAPTURE phase (see its setup above)
        # so it always sees the press before the webview does; since
        # the whole canvas is one opaque WebKit widget from GTK's
        # perspective (unlike a real header bar, whose buttons are
        # separate widgets that claim their own clicks first),
        # unconditionally starting a move here would eat every click
        # meant for those buttons before WebKit ever sees it.
        width = widget.get_width()

        if width > 0 and (x < width * 0.15 or x > width * 0.85):
            return

        self.get_surface().begin_move(
            gesture.get_current_event_device(),
            gesture.get_current_button(),
            bounds.get_x() + x,
            bounds.get_y() + y,
            gesture.get_current_event_time()
        )


    def toggle_fullscreen(self, action, param):

        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def lock_preset_changed(self, action, value):

        action.set_state(value)

        self.preset_locked = value.get_boolean()

        self.show_toast(
            "Preset locked" if self.preset_locked else "Preset unlocked"
        )

    def show_toast(self, text):

        # Adw.Toast text is parsed as Pango markup, so raw &, <, > in
        # the message (e.g. from a JS error string) breaks parsing and
        # silently produces a toast with no text at all.
        toast = Adw.Toast.new(GLib.markup_escape_text(text))
        toast.set_timeout(2)

        self.toast_overlay.add_toast(toast)

    def browse_presets_clicked(self, action, param):

        if self.preset_browser_dialog is None:
            self.build_preset_browser_dialog()

        self.preset_browser_dialog.present(self)

    def build_preset_browser_dialog(self):

        self.preset_list_store = Gtk.StringList.new(self.preset_names)

        expression = Gtk.PropertyExpression.new(
            Gtk.StringObject,
            None,
            "string"
        )

        string_filter = Gtk.StringFilter.new(expression)
        string_filter.set_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)

        filter_model = Gtk.FilterListModel.new(
            self.preset_list_store,
            string_filter
        )

        selection = Gtk.SingleSelection.new(filter_model)

        factory = Gtk.SignalListItemFactory()

        factory.connect("setup", self.preset_row_setup)
        factory.connect("bind", self.preset_row_bind)

        list_view = Gtk.ListView.new(selection, factory)

        list_view.connect("activate", self.preset_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(list_view)
        scrolled.set_vexpand(True)

        search_entry = Gtk.SearchEntry()

        search_entry.connect(
            "search-changed",
            lambda entry: string_filter.set_search(entry.get_text())
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        box.append(search_entry)
        box.append(scrolled)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.set_content(box)

        dialog = Adw.Dialog()
        dialog.set_title("Presets")
        dialog.set_content_width(420)
        dialog.set_content_height(560)
        dialog.set_child(toolbar_view)

        self.preset_browser_dialog = dialog

    def preset_row_setup(self, factory, list_item):

        label = Gtk.Label(xalign=0)

        label.set_margin_start(6)
        label.set_margin_end(6)
        label.set_margin_top(6)
        label.set_margin_bottom(6)

        list_item.set_child(label)

    def preset_row_bind(self, factory, list_item):

        label = list_item.get_child()
        string_object = list_item.get_item()

        label.set_label(string_object.get_string())

    def preset_row_activated(self, list_view, position):

        string_object = list_view.get_model().get_item(position)
        name = string_object.get_string()

        self.run_js(f"loadPresetByName({json.dumps(name)});")

        self.preset_browser_dialog.close()

    def load_preset_clicked(self, action, param):

        dialog = Gtk.FileDialog()
        dialog.set_title("Load Preset")

        preset_filter = Gtk.FileFilter()
        preset_filter.set_name("Presets (MilkDrop / Butterchurn)")
        preset_filter.add_pattern("*.milk")
        preset_filter.add_pattern("*.json")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(preset_filter)
        dialog.set_filters(filters)

        dialog.open(self, None, self.on_preset_file_chosen)

    def on_preset_file_chosen(self, dialog, result, user_data=None):

        try:
            gfile = dialog.open_finish(result)

        except GLib.Error as e:
            print("Load preset cancelled/failed:", e)
            return

        ok, contents, etag = gfile.load_contents(None)

        if not ok:
            print("Failed to read preset file:", gfile.get_path())
            return

        # Base64, not a plain string substitution - MilkDrop preset
        # text is full of quotes/backslashes/newlines that aren't safe
        # to embed directly in a JS string literal.
        encoded = base64.b64encode(contents).decode("ascii")
        name = gfile.get_basename()

        self.run_js(
            f"loadPresetFile({json.dumps(encoded)}, {json.dumps(name)});"
        )

    def next_preset(self, button):

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.run_js("nextPreset();")

    def previous_preset(self, button):

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.run_js("previousPreset();")

    def sensitivity_changed(self, scale):

        self.run_js(f"setSensitivity({scale.get_value()});")

    def build_sensitivity_control(self):

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        label = Gtk.Label(label="Sensitivity", xalign=0)

        box.append(label)

        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            0.0,
            4.0,
            0.1
        )

        scale.set_value(1.0)
        scale.set_size_request(180, -1)
        scale.set_draw_value(False)

        scale.connect(
            "value-changed",
            self.sensitivity_changed
        )

        box.append(scale)

        self.menu_button.get_popover().add_child(box, "sensitivity")



    def audio_source_changed(self, action, value):

        source = value.get_string()

        print("Audio source (Python):", source)

        action.set_state(value)

        if source == "mic":
            self.pinned_sink = None
            self.stop_audio_capture()
            self.run_js("setAudioSource('mic')")
            return

        if source == "none":
            self.pinned_sink = None
            self.stop_audio_capture()
            self.run_js("setAudioSource('none')")
            return

        # Anything else is a specific sink's node name, picked from the
        # dynamically built device list - pin capture to that device
        # (see on_pactl_event for what happens if it later disappears).
        self.pinned_sink = source

        if self.current_sink != source:
            self.restart_audio_monitor(source)

        self.run_js("setAudioSource('system')")


    # System Audio Capture (Pipewire)

    def start_system_audio(self, sink_name=None):

        if sink_name is None:
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

        bus = self.gst_pipeline.get_bus()

        bus.add_signal_watch()

        bus.connect(
            "message",
            self.on_gst_message
        )

        self.gst_pipeline.set_state(
            Gst.State.PLAYING
        )


    def on_gst_message(self, bus, message):

        t = message.type

        if t == Gst.MessageType.ERROR:

            err, debug_info = message.parse_error()

            print("GST ERROR:", err, "|", debug_info)

        elif t == Gst.MessageType.WARNING:

            err, debug_info = message.parse_warning()

            print("GST WARNING:", err, "|", debug_info)


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

        if not self.webview_ready:
            return False

        encoded = base64.b64encode(
            data
        ).decode("ascii")


        self.webview.evaluate_javascript(
            f"receiveAudio('{encoded}')",
            -1,
            None,
            None,
            None,
            self.on_receive_audio_result,
            None
        )

        return False

    def on_receive_audio_result(self, webview, result, user_data):

        try:
            webview.evaluate_javascript_finish(result)

        except Exception as e:
            print("receiveAudio JS error:", e)



    def get_default_sink(self):

        return subprocess.check_output(
            ["pactl", "get-default-sink"]
        ).decode().strip()

    def list_sinks(self):

        raw = subprocess.check_output(
            ["pactl", "-f", "json", "list", "sinks"]
        )

        sinks = json.loads(raw)

        return [
            (sink["name"], sink["description"])
            for sink in sinks
        ]

    def rebuild_audio_source_menu(self):

        self.audio_source_submenu.remove_all()

        devices = Gio.Menu()

        for name, description in self.list_sinks():

            item = Gio.MenuItem.new(description, None)

            item.set_action_and_target_value(
                "win.audio-source",
                GLib.Variant("s", name)
            )

            devices.append_item(item)

        self.audio_source_submenu.append_section(None, devices)

        other = Gio.Menu()

        mic_item = Gio.MenuItem.new("Microphone", None)

        mic_item.set_action_and_target_value(
            "win.audio-source",
            GLib.Variant("s", "mic")
        )

        other.append_item(mic_item)

        none_item = Gio.MenuItem.new("None", None)

        none_item.set_action_and_target_value(
            "win.audio-source",
            GLib.Variant("s", "none")
        )

        other.append_item(none_item)

        self.audio_source_submenu.append_section(None, other)

    def watch_audio_changes(self):

        process = subprocess.Popen(
            ["pactl", "subscribe"],
            stdout=subprocess.PIPE,
            text=True
        )

        for line in process.stdout:

            # "on server" covers default sink/source changes; "on sink"
            # covers devices actually appearing/disappearing (e.g.
            # headphones being switched off), but disconnecting a
            # device fires a whole burst of these (card/sink/module
            # events) within milliseconds of each other. on_pactl_event
            # does several blocking `pactl`/subprocess calls, so running
            # it once per event in that burst was stalling the main
            # thread repeatedly right when a switch happens - hence
            # debouncing to a single run per burst.
            if "on server" in line or "on sink" in line:

                GLib.idle_add(
                    self.schedule_pactl_event
                )

    def schedule_pactl_event(self):

        if self.pactl_event_timer:

            GLib.source_remove(
                self.pactl_event_timer
            )

        self.pactl_event_timer = GLib.timeout_add(
            300,
            self.run_pactl_event
        )

        return False

    def run_pactl_event(self):

        self.pactl_event_timer = None

        self.on_pactl_event()

        return False

    def on_pactl_event(self):

        self.rebuild_audio_source_menu()

        if self.pinned_sink is None:
            return False

        known_sinks = {name for name, _ in self.list_sinks()}

        if self.pinned_sink not in known_sinks:

            # The device the user explicitly picked just disappeared
            # (e.g. headphones switched off/put away). Deliberately NOT
            # auto-restarting capture on some other device here: doing
            # that turned out to be unreliable (a restart triggered
            # from this background watcher - as opposed to one directly
            # triggered by a menu click - would intermittently come up
            # silent, for reasons that trace back to timing in
            # PipeWire's own driver reassignment after a device drops,
            # not anything in this app). Falling back to "None" instead
            # means the only pipeline (re)starts that ever happen are
            # in direct response to the user picking something, which
            # was solid in every test.
            print(
                "Pinned sink",
                self.pinned_sink,
                "disappeared, falling back to None"
            )

            self.lookup_action("audio-source").change_state(
                GLib.Variant("s", "none")
            )

        return False

    def stop_audio_capture(self):

        if self.gst_pipeline:

            self.gst_pipeline.set_state(
                Gst.State.NULL
            )

            self.gst_pipeline = None

        self.current_sink = None

    def restart_audio_monitor(self, sink_name=None):

        if self.gst_pipeline:

            self.gst_pipeline.set_state(
                Gst.State.NULL
            )

            # set_state() only requests the transition - it can finish
            # asynchronously. Without waiting for it here, the new
            # pipeline below gets built (and its pipewiresrc tries to
            # attach) while the old one is still tearing down its
            # PipeWire stream, and the new appsink silently never
            # receives a single sample.
            self.gst_pipeline.get_state(
                Gst.CLOCK_TIME_NONE
            )

        self.start_system_audio(sink_name)

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
