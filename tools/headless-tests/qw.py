import gi, os, json, sys, traceback
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Gdk, Adw
OUT = os.environ["OUT_DIR"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
from melange.cursor_hider import FullscreenCursorHider
from melange import aux_window as A
FullscreenCursorHider.IDLE_SECONDS = 1.0
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def pump(n=5):
    for _ in range(n): GLib.MainContext.default().iteration(False)
def info(title, artist="A"): return {"title": title, "artist": artist, "album": "B", "art_url": "", "status": "Playing", "length_us": 0, "position_us": 0, "player": "test", "identity": "Test", "bus_name": "org.mpris.MediaPlayer2.test"}
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    steps = []
    def at(t, f): steps.append((t, f))
    def s0():
        win.toolbar_hide_delay_scale.set_value(win.toolbar_hide_delay_scale.get_adjustment().get_upper())   # Never
        win.fullscreen(); win.pointer_over_visualizer = True
    at(300, s0)
    def s1():
        win.reveal_toolbar(); win.cursor_hider.on_motion(None, 300, 300)
        check("fullscreen: header shown while moving", win.toolbar_view.get_reveal_top_bars())
    at(900, s1)
    def s2():
        c = win.get_cursor()
        check("fullscreen: header hides with the pointer (even with Hide Delay = Never)", c is not None and c.get_name() == "none" and not win.toolbar_view.get_reveal_top_bars())
        win.unfullscreen()
        # framerate
        caps = []
        orig = win.projectm_view.set_frame_rate_cap; win.projectm_view.set_frame_rate_cap = lambda v: (caps.append(v), orig(v))
        sc = win.framerate_scale
        sc.set_value(60); sc.set_value(61); sc.set_value(30)
        win.projectm_view.set_frame_rate_cap = orig
        check("Framerate: 60 is a real 60 FPS, 61 = Uncapped", caps == [60, 0, 30], str(caps))
        check("Framerate defaults to Uncapped as before", True)
        # placeholder color
        c = Gdk.RGBA(); c.parse("#ff8800"); win.now_playing_text_color_changed(c); pump(3)
        ph = win.now_playing_art_placeholder
        got = ph.get_color()
        check("Now Playing placeholder icon takes the Text Color", abs(got.red - 1) < 0.02 and abs(got.green - 0x88 / 255) < 0.02 and got.blue < 0.02, got.to_string())
        # song change
        win.change_on_new_song_row.set_active(True)
        win.on_now_playing_changed(info("Song 1")); p0 = win.current_preset_name
        win.on_now_playing_changed(info("Song 1"))
        check("same song: preset kept", win.current_preset_name == p0)
        win.on_now_playing_changed(info("Song 2"))
        check("new song: next preset", win.current_preset_name != p0)
        win.lookup_action("lock-preset").change_state(GLib.Variant("b", True)); p1 = win.current_preset_name
        win.on_now_playing_changed(info("Song 3"))
        check("locked: song change doesn't switch", win.current_preset_name == p1)
        win.lookup_action("lock-preset").change_state(GLib.Variant("b", False))
        win.change_on_new_song_row.set_active(False); p2 = win.current_preset_name
        win.on_now_playing_changed(info("Song 4")); check("off: no switch", win.current_preset_name == p2)
        # title
        win.show_preset_in_title_row.set_active(False)
        check("Show Preset Name off → title 'Melange'", win.get_title() == "Melange", win.get_title())
        win.show_preset_in_title_row.set_active(True)
        check("on → preset name back", win.current_preset_name in win.get_title(), win.get_title())
        # reset preferences
        sens = win.sensitivity_scale.get_value(); fr = win.framerate_scale.get_value()
        src = win.lookup_action("audio-source").get_state().unpack()
        win.sensitivity_scale.set_value(sens * 2); win.framerate_scale.set_value(20)
        win.change_on_new_song_row.set_active(True); win.show_preset_in_title_row.set_active(False)
        c = Gdk.RGBA(); c.parse("#00ff00"); win.now_playing_text_color_button.set_rgba(c)
        n_controls = len(win.preference_defaults)
        win.reset_preferences(); pump(3)
        check(f"Reset All Preferences restores defaults ({n_controls} controls)",
              win.sensitivity_scale.get_value() == sens and win.framerate_scale.get_value() == fr and not win.change_preset_on_new_song
              and win.show_preset_in_title and win.now_playing_text_color.to_string() == "rgb(255,255,255)",
              f"{win.sensitivity_scale.get_value()} {win.framerate_scale.get_value()} {win.now_playing_text_color.to_string()}")
        check("…audio source untouched", win.lookup_action("audio-source").get_state().unpack() == src)
        # reset mini visualizers
        aw = A.AuxVisualizerWindow(win, "spectrum"); win.aux_windows["spectrum"] = aw
        aw.gain = 3.0; aw.color_style = "gradient"; aw.save_settings()
        path = f"{OUT}/config/melange/mini_visualizers.json"
        data = json.load(open(path)); data["vu"] = {"vu_style": "needle"}; json.dump(data, open(path, "w"))
        win.reset_mini_visualizers(); pump(3)
        check("Reset All Mini Visualizers: open one back to defaults", aw.gain == 1.0 and aw.color_style == "solid")
        aw.save_settings()
        saved = json.load(open(path)) if os.path.exists(path) else {}
        check("…closed ones forgotten too", "vu" not in saved, str(list(saved)))
        aw.close(); pump(5)
        check("no exceptions", not errors, errors[0][-500:] if errors else "")
        print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
    at(2600, s2)
    for t, f in steps: GLib.timeout_add(t, lambda f=f: (f(), False)[1])
    return False
app.connect_after("activate", lambda a: GLib.timeout_add(300, run, a.get_active_window()))
app.run([])
