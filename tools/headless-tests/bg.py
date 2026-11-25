import gi, os, json, sys, traceback, math
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Gdk
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
def corner(aw):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 120); cr = cairo.Context(surf); aw.on_draw(aw.drawing_area, cr, 200, 120); surf.flush()
    d = surf.get_data(); return d[2], d[1], d[0], d[3]
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    check("Background choices: Color, Main Visualizer, Transparent", [l for _, l in A.BACKGROUND_CHOICES] == ["Color", "Main Visualizer", "Transparent"])
    sp = A.AuxVisualizerWindow(win, "spectrum"); sp.show_labels = False
    rows = sp.settings_rows
    check("no Opacity / Glass / Frosted left", "Background Opacity" not in rows and "Glass Finish" not in rows)
    check("default: Color, the theme's canvas shade", sp.background_mode == "custom" and corner(sp)[:3] in ((13, 13, 15), (230, 230, 232)), str(corner(sp)))
    check("Background Color shown for Color", rows["Background Color"].get_visible())
    sp.background_color.parse("#336699"); check("Color uses the picked color", corner(sp) == (0x33, 0x66, 0x99, 255), str(corner(sp)))
    frame = bytearray()
    for i in range(1024): frame += bytes([200, 60, 20, 255]) if i % 2 else bytes([60, 10, 120, 255])
    for _ in range(25): A.update_main_visualizer_palette(bytes(frame))
    dd = next(w for w in sp.iter_widgets(rows["Background"]) if isinstance(w, Gtk.DropDown)); dd.set_selected(1)
    c = corner(sp); dark = tuple(round(x * 0.55 * 255) for x in A.palette_color("main", 0.0))
    check("Main Visualizer: its darkest color, darkened", sp.background_mode == "main" and all(abs(a - b) <= 2 for a, b in zip(c[:3], dark)) and c[3] == 255, f"{c} vs {dark}")
    check("…Background Color hidden", not rows["Background Color"].get_visible())
    dd.set_selected(2); c = corner(sp)
    check("Transparent: see-through, window class on", c[3] == 0 and sp.has_css_class("transparency-active"), str(c))
    dd.set_selected(0); check("back to Color: solid, class off", corner(sp)[3] == 255 and not sp.has_css_class("transparency-active"))
    path = f"{OUT}/config/melange/mini_visualizers.json"; os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"vu": {"background_mode": "black"}, "xy": {"background_mode": "frosted", "background_opacity": 0.4, "glass_finish": True},
               "peak": {"background_mode": "theme"}, "oscilloscope": {"background_mode": "transparent"}}, open(path, "w"))
    m = {k: A.AuxVisualizerWindow(win, k) for k in ("vu", "xy", "peak", "oscilloscope")}
    check("old Black → Color black", m["vu"].background_mode == "custom" and m["vu"].background_color.to_string() == "rgb(0,0,0)")
    check("old Frosted → Main Visualizer (opacity/glass dropped)", m["xy"].background_mode == "main" and not hasattr(m["xy"], "glass_finish"))
    check("old Match Theme → Color (theme shade)", m["peak"].background_mode == "custom")
    check("Transparent kept", m["oscilloscope"].background_mode == "transparent")
    check("no exceptions", not errors, errors[0][-500:] if errors else "")
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
