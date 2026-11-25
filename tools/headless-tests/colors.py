import gi, os, json, math, random, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Adw, Graphene
import cairo
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
from melange import mini_visualizer_base as B
for _k in dir(B):
    if not hasattr(A, _k): setattr(A, _k, getattr(B, _k))
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def pump(n=5):
    for _ in range(n): GLib.MainContext.default().iteration(False)
def groups(aw):
    page = aw.settings_split.get_sidebar()
    out = []
    for w in aw.iter_widgets(page):
        if isinstance(w, Adw.PreferencesGroup):
            rows = [r.get_title() for r in aw.iter_widgets(w) if isinstance(r, Adw.ActionRow) and r.get_visible() and r.get_parent() and r.get_mapped() is not None and r.is_visible()]
            out.append((w.get_title(), rows))
    return out
def shown(aw):
    return [t for t, r in aw.settings_rows.items() if r.get_visible()]
def colors_rows(aw):
    return [t for t in A.COLOR_ROW_ORDER if t in aw.settings_rows and aw.settings_rows[t].get_visible() and t not in ("Background Opacity", "Glass Finish", "Background Color")]
def sig(): return [math.sin(i * 0.05) * 0.7 + random.uniform(-0.1, 0.1) for i in range(2048)]
def pick(aw, title, key, choices):
    dd = next(w for w in aw.iter_widgets(aw.settings_rows[title]) if isinstance(w, Gtk.DropDown))
    dd.set_selected([k for k, _ in choices].index(key)); pump(2)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    sp = A.AuxVisualizerWindow(win, "spectrum")
    g = groups(sp)
    check("Colors is the first group", g[0][0] == "Colors", str([t for t, _ in g]))
    check("no 'Color Mode' / 'Main Visualizer' in any list", "Color Mode" not in sp.settings_rows and all(k not in ("main", "main-solid") for k, _ in A.PALETTE_CHOICES))
    check("Solid shows: Style, Color, Match, Background", colors_rows(sp) == ["Color Style", "Color", "Peak Hold Color", "Match Main Visualizer", "Background"], str(colors_rows(sp)))
    pick(sp, "Color Style", "gradient", A.STYLE_CHOICES)
    check("Gradient shows: Style, Gradient, Reverse, Match, Background", colors_rows(sp) == ["Color Style", "Gradient", "Reverse", "Peak Hold Color", "Match Main Visualizer", "Background"], str(colors_rows(sp)))
    pick(sp, "Gradient", "gradient", A.PALETTE_CHOICES)
    check("Custom adds Low/High Color", colors_rows(sp) == ["Color Style", "Gradient", "Low Color", "High Color", "Reverse", "Peak Hold Color", "Match Main Visualizer", "Background"], str(colors_rows(sp)))
    pick(sp, "Background", "transparent", A.BACKGROUND_CHOICES); pick(sp, "Background", "custom", A.BACKGROUND_CHOICES)
    check("Custom background shows Background Color", "Background Color" in colors_rows(sp))
    check("Window group keeps Gain/Frame Rate/Compact", [t for t, _ in groups(sp)][-2:] in (["Window", None], ["Window", ""]) or "Window" in [t for t, _ in groups(sp)])
    # line-kind Custom gradient renders through lo/hi
    sp.show_labels = False; sp.background_mode = "black"
    for _ in range(4): sp.push_audio(sig(), sig())
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 120); cr = cairo.Context(surf); sp.on_draw(sp.drawing_area, cr, 300, 120); surf.write_to_png(f"{OUT}/custom-line.png")
    check("color_mode back to derived after a draw", sp._color_mode_override is None and sp.color_mode == "gradient")
    vu = A.AuxVisualizerWindow(win, "vu")
    check("VU Classic shows Style, Peak Hold Color, Match, Background", colors_rows(vu) == ["Color Style", "Peak Hold Color", "Match Main Visualizer", "Background"], str(colors_rows(vu)))
    sg = A.AuxVisualizerWindow(win, "spectrogram")
    check("Spectrogram: Gradient (no Style), High Level, Reverse, Match, Background", colors_rows(sg) == ["Gradient", "High Level", "Reverse", "Match Main Visualizer", "Background"], str(colors_rows(sg)))
    pp = A.AuxVisualizerWindow(win, "pipes")
    check("Pipes: band colors, Pulse Color, Match, Background", colors_rows(pp) == ["Bass Color", "Low Mid Color", "High Mid Color", "Treble Color", "Pulse Color", "Match Main Visualizer", "Background"], str(colors_rows(pp)))
    dv = A.AuxVisualizerWindow(win, "dvd")
    check("DVD: Match, Background, Background Color (custom by default)", colors_rows(dv) == ["Match Main Visualizer", "Background"], str(colors_rows(dv)))
    # migration
    path = f"{OUT}/config/melange/mini_visualizers.json"; os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"xy": {"color_mode": "fire"}, "vectorscope": {"color_mode": "main"}, "oscilloscope": {"color_mode": "main-solid"},
               "peak": {"color_mode": "classic"}, "terrain": {"terrain_palette": "main"}, "waterfall": {"waterfall_palette": "ocean"}}, open(path, "w"))
    m = {k: A.AuxVisualizerWindow(win, k) for k in ("xy", "vectorscope", "oscilloscope", "peak", "terrain", "waterfall")}
    check("old palette Color Mode → Gradient = that palette", m["xy"].color_style == "gradient" and m["xy"].line_gradient == "fire" and m["xy"].color_mode == "fire")
    check("old Main Visualizer Gradient → Gradient + Match 100%", m["vectorscope"].color_style == "gradient" and m["vectorscope"].color_sync == 1.0)
    check("old Main Visualizer Color → Solid + Match 100%", m["oscilloscope"].color_style == "solid" and m["oscilloscope"].color_sync == 1.0)
    check("old Classic stays Classic", m["peak"].color_style == "classic")
    check("old heatmap Main Visualizer → Rainbow + Match 100%", m["terrain"].terrain_palette == "rainbow" and m["terrain"].color_sync == 1.0)
    check("other heatmap palettes unchanged", m["waterfall"].waterfall_palette == "ocean" and m["waterfall"].color_sync == 0.0)
    for w in list(m.values()) + [sp, vu, sg, pp, dv]: w.close()
    pump(10)
    for k in ("spectrum", "vu"):
        w = A.AuxVisualizerWindow(win, k)
        own = w.settings_rows.get("Style"); col = w.settings_rows.get("Color Style")
        check(f"{k}: shape Style stays in its own group, Color Style in Colors", own is not None and col is not None and own.get_parent() is not col.get_parent())
        w.close()
    check("no exceptions", not errors, errors[0][-600:] if errors else "")
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
