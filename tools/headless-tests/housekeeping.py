# Reduced permissions still render and capture audio; Remove from Your Presets.
import gi, os, json, hashlib
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Graphene
OUT = os.environ["OUT_DIR"]; SRC = os.environ["SRC_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import aux_window as A
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def grab(win, v):
    w, h = v.get_width(), v.get_height(); node = None
    for _ in range(10):
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(v).snapshot(snap, w, h); node = snap.to_node()
        if node: break
        GLib.MainContext.default().iteration(False)
    tex = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)); tex.save_to_png(f"{OUT}/f.png")
    return hashlib.md5(open(f"{OUT}/f.png", "rb").read()).hexdigest()
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    win.lookup_action("audio-source").change_state(GLib.Variant("s", "melange_leaktest"))
    aw = A.AuxVisualizerWindow(win, "vu"); win.aux_windows["vu"] = aw
    st = {"aux": 0}
    orig = aw.push_audio
    aw.push_audio = lambda l, r: (st.__setitem__("aux", st["aux"] + 1), orig(l, r))
    def a(): st["f1"] = grab(win, win.projectm_view)
    def b():
        check("projectM renders (frames change)", grab(win, win.projectm_view) != st["f1"])
        check("system audio is captured and reaches mini visualizers", st["aux"] > 20, f"{st['aux']} chunks")
        path = f"{SRC}/My Cool Preset.milk"
        win.load_preset_file(Gio.File.new_for_path(path))
        check("opened preset is in Your Presets", "My Cool Preset" in win.user_loaded_presets and win.presets.contains("My Cool Preset"))
        win.remove_user_preset("My Cool Preset")
        saved = json.load(open(f"{OUT}/config/melange/user-presets.json"))
        root = win.preset_tree_root
        top = root.get_item(0).title if root.get_n_items() else ""
        check("Remove: gone from the list, the catalog and the saved file", not win.presets.contains("My Cool Preset") and saved == [] and not top.startswith("Your Presets"), top)
        check("the file itself is kept", os.path.exists(path))
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    GLib.timeout_add(1500, lambda: (a(), False)[1]); GLib.timeout_add(4500, lambda: (b(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
