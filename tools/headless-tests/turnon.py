import gi, os
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
class FakeGesture:
    def __init__(self, widget): self.widget = widget
    def get_widget(self): return self.widget
    def get_current_event_device(self): return None
    def get_current_button(self): return 1
    def get_current_event_time(self): return 0
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    win.set_default_size(1000, 640)
    moves = []
    def a():
        win.lookup_action("main-visualizer").change_state(GLib.Variant("b", False))
    def b():
        surface = win.get_surface()
        orig = surface.begin_move
        win.get_surface = lambda: type("S", (), {"begin_move": lambda self, *a: moves.append(a)})()
        box = win.content_box; button = win.main_off_page.get_child()
        ok, r = button.compute_bounds(box)
        cx, cy = r.get_x() + r.get_width() / 2, r.get_y() + r.get_height() / 2
        win.on_window_drag_pressed(FakeGesture(box), 1, cx, cy)
        check("press on Turn On is not taken as a window drag", len(moves) == 0, f"button at {cx:.0f},{cy:.0f}")
        win.on_window_drag_pressed(FakeGesture(box), 1, box.get_width() / 2, 40)
        check("press on empty space still drags the window", len(moves) == 1)
        button.emit("clicked")
        check("Turn On turns it on", win.main_visualizer_on and win.projectm_view.get_visible())
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    GLib.timeout_add(800, lambda: (a(), False)[1]); GLib.timeout_add(1800, lambda: (b(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
