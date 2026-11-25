import gi, os, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Gdk, Graphene
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def widgets(w):
    yield w; c = w.get_first_child()
    while c: yield from widgets(c); c = c.get_next_sibling()
def shot(widget, name):
    w, h = widget.get_width(), widget.get_height()
    for _ in range(8):
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h); node = snap.to_node()
        if node: break
        GLib.MainContext.default().iteration(False)
    if node: widget.get_root().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)).save_to_png(f"{OUT}/{name}.png")
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    narrow = A.AuxVisualizerWindow(win, "vu"); narrow.set_default_size(110, 320); narrow.present()
    wide = A.AuxVisualizerWindow(win, "spectrum"); wide.set_default_size(420, 300); wide.present()
    st = {}
    def a():
        wide.settings_button.set_active(True)
        check("wide window: sidebar as before", wide.settings_split.get_show_sidebar() and wide.settings_window is None)
        wide.settings_split.set_show_sidebar(False)
        narrow.settings_button.set_active(True)
    def b():
        sw = narrow.settings_window
        check("narrow window: settings open in their own window", sw is not None and sw.get_visible() and not narrow.settings_split.get_show_sidebar())
        check("…titled for the visualizer, beside it (transient)", sw.get_title() == "VU Meter Settings" and sw.get_transient_for() is narrow, sw.get_title())
        check("…a usable size", sw.get_width() >= 300, f"{sw.get_width()}x{sw.get_height()}")
        dd = next(w for w in widgets(sw) if isinstance(w, Gtk.DropDown) and narrow.settings_rows.get("Gradient") and w.is_ancestor(narrow.settings_rows["Color Style"]))
        dd.set_selected(1)
        check("controls in the window change the visualizer live", narrow.color_style == "solid")
        shot(sw, "settings-window")
        narrow.lookup_action("compact-mode").change_state(GLib.Variant("b", True))
    def c():
        sw = narrow.settings_window
        sw_switch = narrow.settings_rows["Compact Mode"].get_activatable_widget()
        check("a change from the right-click menu shows in the window", sw_switch.get_active() and sw_switch.get_root() is sw)
        narrow.settings_button.set_active(True)   # toggle again = close
    def d():
        check("toggle again closes the window", narrow.settings_window is None and not narrow.settings_button.get_active())
        check("sidebar controls show the change afterwards", narrow.settings_rows["Color Style"].get_root() is narrow and narrow.color_style == "solid")
        narrow.lookup_action("show-settings").activate(None)
    def e():
        sw = narrow.settings_window
        check("right-click menu Settings also opens the window", sw is not None)
        sw.lookup_action if False else None
        ctl = [c for c in list(sw.observe_controllers()) if isinstance(c, Gtk.EventControllerKey)][0]
        ctl.emit("key-pressed", Gdk.KEY_Escape, 0, 0)
    def f():
        check("Esc closes it", narrow.settings_window is None)
        narrow.settings_button.set_active(True)
    def g():
        sw = narrow.settings_window
        narrow.close()
    def h():
        check("closing the visualizer closes its settings window", all(w.get_title() != "VU Meter Settings" for w in Gtk.Window.list_toplevels() if w.get_visible()))
        check("no exceptions", not errors, errors[0][-500:] if errors else "")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t, fn in enumerate((a, b, c, d, e, f, g, h)): GLib.timeout_add(800 + t * 700, lambda fn=fn: (fn(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
