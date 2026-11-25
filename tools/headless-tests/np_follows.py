# Now Playing "Text Follows" / "Background Follows": Chosen Color,
# System Theme, Main Visualizer.
import gi, os, json
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gdk, Adw
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange import mini_visualizer_base as B
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def css(win): return win.now_playing_placeholder_css.to_string()
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    sm = Adw.StyleManager.get_default()
    text_row, bg_row = win.now_playing_text_source_row, win.now_playing_bg_source_row
    check("defaults: Chosen Color, colour rows shown", text_row.get_selected() == 0 and win.now_playing_text_color_row.get_visible())
    sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    text_row.set_selected(1); bg_row.set_selected(1)
    t, b = win.now_playing_effective_colors()
    check("System Theme, dark: white text on a dark card", t.to_string() == "rgb(255,255,255)" and b.red < 0.2, f"{t.to_string()} on {b.to_string()}")
    check("colour rows hidden when not Chosen Color", not win.now_playing_text_color_row.get_visible() and not win.now_playing_bg_rows[0].get_visible())
    sm.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    t, b = win.now_playing_effective_colors()
    check("System Theme follows a switch to light", b.red > 0.9 and t.red < 0.2 and "rgb(31,31,33)" in css(win), f"{t.to_string()} on {b.to_string()}")
    # Main Visualizer, with a sampled frame (deep blue + bright cyan)
    frame = bytearray()
    for i in range(1024): frame += bytes([20, 230, 255, 255]) if i % 2 else bytes([10, 20, 90, 255])
    v0 = B.MAIN_PALETTE_VERSION[0]
    text_row.set_selected(2); bg_row.set_selected(2)
    for _ in range(25): win.on_projectm_colors(bytes(frame))
    check("sampling runs for Now Playing alone (no mini visualizer open)", not win.aux_windows and B.MAIN_PALETTE_VERSION[0] > v0)
    t, b = win.now_playing_effective_colors()
    check("Main Visualizer: bright text on its dark colour", t.green > 0.7 and b.red < 0.15 and b.blue > b.red, f"{t.to_string()} on {b.to_string()}")
    # low contrast -> white/black fallback
    gray = bytearray()
    for i in range(1024): gray += bytes([120, 120, 120, 255])
    for _ in range(40): win.on_projectm_colors(bytes(gray))
    bg_row.set_selected(0); c = Gdk.RGBA(); c.parse("#f0f0f0"); win.now_playing_bg_color_button.set_rgba(c)
    t, b = win.now_playing_effective_colors()
    check("matched text too close to the background falls back to black/white", t.to_string() in ("rgb(0,0,0)", "rgb(255,255,255)"), f"{t.to_string()} on {b.to_string()}")
    text_row.set_selected(0); c2 = Gdk.RGBA(); c2.parse("#ff8800"); win.now_playing_text_color_button.set_rgba(c2)
    t, b = win.now_playing_effective_colors()
    check("Chosen Color is used as-is", t.to_string() == "rgb(255,136,0)")
    text_row.set_selected(2); bg_row.set_selected(1)
    win.save_settings()
    prefs = json.load(open(f"{OUT}/config/melange/settings.json"))["preferences"]
    check("choices are remembered", prefs.get("now-playing/Text Follows") == 2 and prefs.get("now-playing/Background Follows") == 1, str({k: v for k, v in prefs.items() if "Follows" in k}))
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(500, run, a.get_active_window()))
app.run([])
