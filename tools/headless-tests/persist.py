import gi, os, json
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Adw, Gdk
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def pump(n=10):
    for _ in range(n): GLib.MainContext.default().iteration(False)
def snapshot(aw): return {n: A.encode_setting(getattr(aw, n)) for n in A.SAVED_SETTINGS}
def widgets(w):
    yield w; c = w.get_first_child()
    while c: yield from widgets(c); c = c.get_next_sibling()
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    # 1. change controls through the UI on one window, close, reopen
    aw = A.AuxVisualizerWindow(win, "spectrum"); aw.present(); pump(20)
    side = aw.settings_split.get_sidebar()
    sw = next(w for w in widgets(side) if isinstance(w, Gtk.Switch)); sw.set_active(not sw.get_active()); want_switch = sw.get_active()
    sc = next(w for w in widgets(side) if isinstance(w, Gtk.Scale)); a = sc.get_adjustment(); a.set_value(a.get_upper()); want_scale = a.get_value()
    cb = next(w for w in widgets(side) if isinstance(w, Gtk.ColorDialogButton)); c = Gdk.RGBA(); c.parse("#ff00aa"); cb.set_rgba(c)
    dd = next(w for w in widgets(side) if isinstance(w, Gtk.DropDown)); dd.set_selected(1)
    aw.set_default_size(140, 333); pump(10)
    before = snapshot(aw)
    aw.close(); pump(20)
    data = json.load(open(f"{OUT}/config/melange/mini_visualizers.json"))
    check("saved on close, with size", data["spectrum"]["window_size"] == [140, 333], str(data["spectrum"].get("window_size")))
    aw2 = A.AuxVisualizerWindow(win, "spectrum"); aw2.present(); pump(20)
    check("settings restored on reopen", snapshot(aw2) == before)
    check("size restored", aw2.get_default_size() == (140, 333), str(aw2.get_default_size()))
    side2 = aw2.settings_split.get_sidebar()
    sw2 = next(w for w in widgets(side2) if isinstance(w, Gtk.Switch)); sc2 = next(w for w in widgets(side2) if isinstance(w, Gtk.Scale))
    cb2 = next(w for w in widgets(side2) if isinstance(w, Gtk.ColorDialogButton)); dd2 = next(w for w in widgets(side2) if isinstance(w, Gtk.DropDown))
    check("controls show restored values", sw2.get_active() == want_switch and abs(sc2.get_value() - want_scale) < 1e-6 and cb2.get_rgba().to_string() == "rgb(255,0,170)" and dd2.get_selected() == 1,
          f"{sw2.get_active()} {sc2.get_value()} {cb2.get_rgba().to_string()} {dd2.get_selected()}")
    # debounced save without closing
    dd2.set_selected(0)
    def later():
        d = json.load(open(f"{OUT}/config/melange/mini_visualizers.json"))
        check("change saved within ~1s without closing", d["spectrum"]["spectrum_style"] == aw2.spectrum_style, d["spectrum"]["spectrum_style"])
        aw2.close(); pump(10)
        # 2. every kind round-trips (every value nudged where possible)
        for kind in A.AUX_WINDOW_TITLES:
            w = A.AuxVisualizerWindow(win, kind); w.present(); pump(5)
            for n in A.SAVED_SETTINGS:
                v = getattr(w, n)
                if isinstance(v, bool): setattr(w, n, not v)
                elif isinstance(v, int): setattr(w, n, v + 1)
                elif isinstance(v, float): setattr(w, n, v * 0.9 + 0.01)
            snap = snapshot(w); w.close(); pump(10)
            w = A.AuxVisualizerWindow(win, kind); w.present(); pump(10)
            diff = [n for n in A.SAVED_SETTINGS if A.encode_setting(getattr(w, n)) != snap[n]]
            check(f"{kind}: all settings round-trip", not diff, str(diff[:4]))
            w.close(); pump(10)
        # 3. corrupted / wrong-typed file -> defaults, no crash
        open(f"{OUT}/config/melange/mini_visualizers.json", "w").write('{"vu": {"color": 5, "vu_segments": "lots", "window_size": "big", "vu_style": 3}, "dvd": {"icon_name": "nonexistent"}}')
        w = A.AuxVisualizerWindow(win, "vu"); w.present(); pump(10)
        check("wrong types ignored", w.vu_segments == 15 and w.vu_style == "bars" and w.get_default_size() == (360, 220), f"{w.vu_segments} {w.vu_style} {w.get_default_size()}")
        w.close(); pump(5)
        w = A.AuxVisualizerWindow(win, "dvd"); w.present(); pump(10)
        check("unknown DVD icon ignored", w.icon_name == A.DVD_ICON_CHOICES[0][0]); w.close(); pump(5)
        open(f"{OUT}/config/melange/mini_visualizers.json", "w").write("not json{")
        w = A.AuxVisualizerWindow(win, "peak"); w.present(); pump(10)
        check("garbage file -> defaults", w.peak_hold_seconds == 1.5); w.close(); pump(10)
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    GLib.timeout_add(1600, lambda: (later(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
