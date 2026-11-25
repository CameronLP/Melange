import gi, os, sys, traceback, math, random
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Gdk, Graphene
import cairo
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
def sig(): return [math.sin(i * 0.05) * 0.7 + random.uniform(-0.2, 0.2) for i in range(2048)]
def centroid(aw):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 300); cr = cairo.Context(surf); aw.on_draw(aw.drawing_area, cr, 400, 300); surf.flush()
    d = surf.get_data(); st = surf.get_stride(); xs = []; ys = []
    for y in range(0, 300, 3):
        for x in range(0, 400, 3):
            if d[y * st + x * 4 + 3] > 40: xs.append(x); ys.append(y)
    return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else (0, 0)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    st = {}
    def a():
        win.lookup_action("main-visualizer").change_state(GLib.Variant("b", False))
        win.set_default_size(1280, 760)
    def b():
        page = win.main_off_page; ok, r = page.compute_bounds(win)
        st["big"] = (r.get_x(), r.get_width(), win.get_width())
        check("off page fills the visualizer area (1280 wide)", r.get_width() > win.get_width() * 0.6, f"page {r.get_width():.0f} of window {win.get_width()}")
        button = page.get_child(); ok2, rb = button.compute_bounds(win)
        mid = rb.get_x() + rb.get_width() / 2; area_mid = r.get_x() + r.get_width() / 2
        check("Turn On button centred in it", abs(mid - area_mid) < 3, f"{mid:.0f} vs {area_mid:.0f}")
        win.set_default_size(800, 500)
    def c():
        page = win.main_off_page; ok, r = page.compute_bounds(win)
        check("follows a resize (800 wide)", r.get_width() > win.get_width() * 0.6 and r.get_width() < st["big"][1], f"page {r.get_width():.0f} of window {win.get_width()}")
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height()); node = snap.to_node()
        if node: win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, win.get_width(), win.get_height())).save_to_png(f"{OUT}/off.png")
        win.lookup_action("main-visualizer").change_state(GLib.Variant("b", True))
        # 3D pan
        t = A.AuxVisualizerWindow(win, "terrain"); t.background_mode = "transparent"
        for _ in range(8): t.push_audio(sig(), sig()); t.last_terrain_frame_time = 0
        c0 = centroid(t)
        t.on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType.SHIFT_MASK)
        t.on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType.SHIFT_MASK)
        t.terrain_dirty = True; c1 = centroid(t)
        check("Shift+→ moves the scene right (not rotate)", c1[0] > c0[0] + 20 and abs(t.terrain_azimuth - math.radians(35)) < 1e-9, f"x {c0[0]:.0f} -> {c1[0]:.0f}")
        az = t.terrain_azimuth; t.on_key_pressed(None, Gdk.KEY_Right, 0, 0)
        check("plain → still rotates", t.terrain_azimuth != az)
        t.set_view_pan(0.2, -0.1); t.center_view()
        check("Center View recentres, keeps angle", t.view_pan() == (0.0, 0.0) and t.terrain_azimuth != math.radians(35))
        t.set_view_pan(0.3, 0.3); t.reset_view()
        check("Reset View also recentres", t.view_pan() == (0.0, 0.0))
        t.on_context_menu_pressed(None, 1, 20, 20)
        m = t.context_menu.get_menu_model(); labels = [m.get_item_attribute_value(i, "label").get_string() for i in range(m.get_n_items())]
        check("right-click menu has Center View", "Center View" in labels, str(labels)); t.context_menu.popdown()
        btn = next((w for w in t.iter_widgets(t.settings_split.get_sidebar()) if isinstance(w, Gtk.Button) and w.get_label() == "Center View"), None)
        t.set_view_pan(0.4, 0); btn.emit("clicked")
        check("settings Center View button works", btn is not None and t.view_pan() == (0.0, 0.0))
        p = A.AuxVisualizerWindow(win, "pipes"); p.set_view_pan(0.25, 0); p.save_settings(); p.close()
        p2 = A.AuxVisualizerWindow(win, "pipes")
        check("pan is remembered", abs(p2.pipes_pan_x - 0.25) < 1e-9)
        check("no exceptions", not errors, errors[0][-500:] if errors else "")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    for t_, f in ((800, a), (2000, b), (3200, c)): GLib.timeout_add(t_, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
