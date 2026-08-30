import ctypes
import gi
import json
import os
import subprocess
import threading

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, Gst
from melange.projectm_view import ProjectMView

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

        # Visualizer

        self.projectm_view = ProjectMView()
        self.projectm_view.connect("ready", self.on_projectm_ready)

        self.content_box.append(
            self.projectm_view
        )

        # Lets the window be dragged from anywhere, not just the
        # header bar. Runs in the CAPTURE phase on content_box (the
        # visualizer's parent) rather than the visualizer itself, since
        # begin_move() has to fire before the child widget's own click
        # handling - a real header bar's buttons are separate widgets
        # that claim their own clicks first, but there's nothing
        # equivalent to defer to here.
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
        self.preset_locked = False
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

        # Setting the initial state above only sets the action's
        # internal value - it does not fire "change-state" (that only
        # happens on a real activation, e.g. clicking the menu item),
        # so without this nothing would actually get pinned to the
        # current sink until the user opened the menu themselves.
        action.change_state(
            GLib.Variant("s", self.current_sink)
        )

        # Preset/fullscreen controls, exposed as actions so they get
        # real keyboard accelerators via
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



    def on_projectm_ready(self, projectm_view):

        projectm_view.playlist.set_switched_callback(self.on_preset_switched)
        projectm_view.set_sensitivity(1.0)

        # data/presets (projectM's own default set, presets_projectM
        # from projectM-SDL) installed alongside this module - a real
        # /usr/... host path can't be exposed into the sandbox at all
        # (Flatpak rejects "/usr" as reserved), which a bundled preset
        # directory sidesteps entirely regardless.
        pkgdatadir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        projectm_view.playlist.add_path(
            os.path.join(pkgdatadir, "presets"),
            recurse_subdirs=True
        )

        if projectm_view.playlist.size() > 0:
            projectm_view.playlist.set_position(0, hard_cut=False)

    def on_preset_switched(self, is_hard_cut, index):

        name = self.projectm_view.playlist.item(index)

        if name:
            display_name = os.path.splitext(os.path.basename(name))[0]

            self.set_title(f'Melange - "{display_name}"')

        self.projectm_view.unstick()


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
        self.projectm_view.set_locked(self.preset_locked)

        self.show_toast(
            "Preset locked" if self.preset_locked else "Preset unlocked"
        )

    def show_toast(self, text):

        toast = Adw.Toast.new(text)
        toast.set_timeout(2)

        self.toast_overlay.add_toast(toast)

    def browse_presets_clicked(self, action, param):

        if not self.projectm_view.playlist:
            self.show_toast("Visualizer is still starting up")
            return

        if self.preset_browser_dialog is None:
            self.build_preset_browser_dialog()

        items = self.projectm_view.playlist.items()

        self.preset_list_store.splice(
            0,
            self.preset_list_store.get_n_items(),
            items
        )

        self.preset_browser_dialog.present(self)

    def build_preset_browser_dialog(self):

        self.preset_list_store = Gtk.StringList.new([])

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
        path = string_object.get_string()

        # position is an index into the (possibly search-filtered)
        # GtkListView model, which won't match the playlist's own
        # ordering once a filter is active - set_position() needs the
        # real playlist index, found by matching the path instead.
        try:
            playlist_index = self.projectm_view.playlist.items().index(path)
            self.projectm_view.playlist.set_position(playlist_index, hard_cut=False)

        except ValueError:
            self.projectm_view.load_preset_file(path)

        self.preset_browser_dialog.close()

    def load_preset_clicked(self, action, param):

        dialog = Gtk.FileDialog()
        dialog.set_title("Load Preset")

        milk_filter = Gtk.FileFilter()
        milk_filter.set_name("MilkDrop Presets")
        milk_filter.add_pattern("*.milk")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(milk_filter)
        dialog.set_filters(filters)

        dialog.open(self, None, self.on_preset_file_chosen)

    def on_preset_file_chosen(self, dialog, result, user_data=None):

        try:
            gfile = dialog.open_finish(result)

        except GLib.Error as e:
            print("Load preset cancelled/failed:", e)
            return

        if not self.projectm_view.playlist:
            self.show_toast("Visualizer is still starting up")
            return

        path = gfile.get_path()

        if not path:
            print("Preset file has no local path (not on this filesystem?):", gfile.get_uri())
            return

        # projectm_load_preset_file() takes a real filesystem path
        # directly and parses/renders .milk natively - unlike the old
        # Butterchurn path, there's no conversion step or JS round
        # trip needed here at all.
        self.projectm_view.load_preset_file(path)
        self.projectm_view.playlist.add_preset(path)

        self.set_title(f'Melange - "{os.path.splitext(os.path.basename(path))[0]}"')

    def next_preset(self, button):

        if not self.projectm_view.playlist:
            return

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.projectm_view.playlist.play_next(hard_cut=False)

    def previous_preset(self, button):

        if not self.projectm_view.playlist:
            return

        if self.preset_locked:
            self.show_toast("Preset is locked")
            return

        self.projectm_view.playlist.play_previous(hard_cut=False)

    def sensitivity_changed(self, scale):

        self.projectm_view.set_sensitivity(scale.get_value())

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
            self.restart_audio_monitor(self.get_default_source(), is_source=True)
            return

        if source == "none":
            self.pinned_sink = None
            self.stop_audio_capture()
            return

        # Anything else is a specific sink's node name, picked from the
        # dynamically built device list - pin capture to that device
        # (see on_pactl_event for what happens if it later disappears).
        self.pinned_sink = source

        if self.current_sink != source:
            self.restart_audio_monitor(source)


    # System Audio Capture (Pipewire)

    def start_system_audio(self, node_name=None, is_source=False):

        if node_name is None:
            node_name = self.get_default_sink()

        self.current_sink = node_name

        print("Capturing", "source" if is_source else "sink monitor", "of:", node_name)

        # target-object must be the node's own name (pipewiresrc only
        # matches real PipeWire node names/serials, and the deprecated
        # `path` property only takes numeric ids).
        #
        # stream.capture.sink=true is what taps a sink's monitor ports
        # instead of opening a real capture device on it - without it,
        # WirePlumber may treat this as a microphone request and, on
        # Bluetooth sinks, drop the a2dp connection to the lower
        # quality headset profile. It's the wrong thing to set when
        # actually capturing a source (mic) though, since there we do
        # want a normal capture stream.
        stream_props = (
            "" if is_source else
            'stream-properties="props,stream.capture.sink=true"'
        )

        self.gst_pipeline = Gst.parse_launch(
            f"""
            pipewiresrc
            target-object="{node_name}"
            {stream_props}
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
                    self.feed_pcm_to_projectm,
                    data
                )

        return Gst.FlowReturn.OK

    def feed_pcm_to_projectm(self, data):

        # S16LE stereo interleaved, straight from the GStreamer caps in
        # start_system_audio - projectm_pcm_add_int16 takes this format
        # directly, no conversion needed.
        samples = (ctypes.c_int16 * (len(data) // 2)).from_buffer_copy(data)

        self.projectm_view.pcm_add_int16(samples)

        return False



    def get_default_sink(self):

        return subprocess.check_output(
            ["pactl", "get-default-sink"]
        ).decode().strip()

    def get_default_source(self):

        return subprocess.check_output(
            ["pactl", "get-default-source"]
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

    def restart_audio_monitor(self, node_name=None, is_source=False):

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

        self.start_system_audio(node_name, is_source)

        return False


