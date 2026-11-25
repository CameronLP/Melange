# Main window settings survive a restart (run twice: PHASE=1 then PHASE=2,
# same OUT_DIR).
import gi, os, json
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gdk, Adw, Gtk
OUT = os.environ["OUT_DIR"]; PHASE = os.environ["PHASE"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def labels(m):
    out = []
    for i in range(m.get_n_items()):
        v = m.get_item_attribute_value(i, "label", None)
        if v: out.append(v.get_string())
        sec = m.get_item_link(i, "section")
        if sec: out += labels(sec)
    return out
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    sm = Adw.StyleManager.get_default()
    if PHASE == "1":
        win.sensitivity_scale.set_value(2.5)
        win.framerate_scale.set_value(30)
        win.now_playing_outline_row.set_active(True)
        c = Gdk.RGBA(); c.parse("#ff8800"); win.now_playing_text_color_button.set_rgba(c)
        win.theme_buttons[int(Adw.ColorScheme.FORCE_DARK)].set_active(True)
        win.lookup_action("now-playing-enabled").change_state(GLib.Variant("b", True))
        shuffle = win.lookup_action("shuffle-preset").get_state().get_boolean()
        win.lookup_action("shuffle-preset").change_state(GLib.Variant("b", not shuffle))
        open(f"{OUT}/shuffle", "w").write(str(not shuffle))
        win.set_default_size(1000, 640)
        def later():
            win.on_content_right_click(None, 1, 100, 100)
            c = win.content_box.get_first_child(); pops = []
            while c:
                if isinstance(c, Gtk.PopoverMenu): pops.append(c)
                c = c.get_next_sibling()
            got = labels(pops[-1].get_menu_model())
            check("right-click menu has Transparency Mode and Now Playing", "Transparency Mode" in got and "Now Playing" in got, str(got))
            pops[-1].popdown()
            app.quit_saving()
            data = json.load(open(f"{OUT}/config/melange/settings.json"))
            check("Quit saved settings.json", data["preferences"].get("visualizer/Sensitivity") == 2.5 and data["window_size"] == [1000, 640], str(data.get("window_size")))
            check("Audio Source isn't saved", not any("Audio Source" in k for k in data["preferences"]))
            print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
        GLib.timeout_add(1500, lambda: (later(), False)[1])
    else:
        shuffle = open(f"{OUT}/shuffle").read() == "True"
        check("slider restored (Sensitivity 2.5)", win.sensitivity_scale.get_value() == 2.5, str(win.sensitivity_scale.get_value()))
        check("Framerate restored (30)", win.framerate_scale.get_value() == 30)
        check("switch restored (Text Outline on)", win.now_playing_outline_row.get_active() and win.now_playing_outline)
        check("colour restored and applied", win.now_playing_text_color.to_string() == "rgb(255,136,0)", win.now_playing_text_color.to_string())
        check("theme restored (dark)", sm.get_color_scheme() == Adw.ColorScheme.FORCE_DARK)
        check("Now Playing and Shuffle restored", win.lookup_action("now-playing-enabled").get_state().get_boolean() and win.lookup_action("shuffle-preset").get_state().get_boolean() == shuffle)
        check("window size restored", win.get_default_size() == (1000, 640), str(win.get_default_size()))
        win.reset_preferences()
        check("Reset All Preferences still goes to the real defaults", win.sensitivity_scale.get_value() != 2.5 and win.framerate_scale.get_value() == 61 and not win.now_playing_outline)
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(500, run, a.get_active_window()))
app.run([])
