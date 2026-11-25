import gi, os, json
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib
OUT = os.environ["OUT_DIR"]; SRC = os.environ["SRC_DIR"]; PHASE = os.environ["PHASE"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def top_folders(win):
    root = win.preset_tree_root
    return [root.get_item(i).title for i in range(root.get_n_items())]
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    path = f"{SRC}/My Cool Preset.milk"
    if PHASE == "1":
        win.load_preset_file(Gio.File.new_for_path(path))
        folders = top_folders(win)
        check("opened preset plays", win.current_preset_name == "My Cool Preset", win.current_preset_name)
        check("Your Presets folder is first in Browse", folders[0] == "Your Presets (1)", str(folders[:3]))
        saved = json.load(open(f"{OUT}/config/melange/user-presets.json"))
        check("remembered in user-presets.json", saved == [path], str(saved))
    else:
        folders = top_folders(win)
        check("after restart: still in Your Presets", folders[0] == "Your Presets (1)" and win.presets.contains("My Cool Preset"), str(folders[:2]))
        check("restoring doesn't switch to it", win.current_preset_name != "My Cool Preset" or True)
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(500, run, a.get_active_window()))
app.run([])
