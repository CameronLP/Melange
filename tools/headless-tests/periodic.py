# Periodic Fade: longer defaults/ranges, squared sliders, and old saved
# settings (plain seconds, no version) converted on load.
import gi, os, json, math
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib
OUT = os.environ["OUT_DIR"]; PHASE = os.environ["PHASE"]
GLib.get_user_config_dir = lambda: f"{OUT}/config"
if PHASE == "old":
    os.makedirs(f"{OUT}/config/melange", exist_ok=True)
    json.dump({"preferences": {"now-playing/Hidden For": 24.264705882352942, "now-playing/Shown For": 29.580882352941174, "now-playing/Fade Duration": 5.0}},
              open(f"{OUT}/config/melange/settings.json", "w"))
Gio.Resource.load("/app/share/melange/melange.gresource")._register()
from melange.main import MelangeApplication
app = MelangeApplication(); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
results = []
def check(label, ok, detail=""):
    ok = bool(ok); results.append(ok); print(f"{'PASS' if ok else 'FAIL'} {label} {detail}", flush=True)
def run(win):
    if win.projectm_view is None or not win.projectm_view.size_known: return True
    h, sh, fd = win.now_playing_periodic_hidden_scale, win.now_playing_periodic_shown_scale, win.now_playing_periodic_fade_duration_scale
    if PHASE == "fresh":
        check("defaults: hidden 30 s, shown 15 s, fade 2 s",
              win.now_playing_periodic_hidden_seconds == 30 and win.now_playing_periodic_shown_seconds == 15 and win.now_playing_periodic_fade_duration == 2.0)
        h.set_value(h.get_adjustment().get_upper()); sh.set_value(sh.get_adjustment().get_upper()); fd.set_value(fd.get_adjustment().get_upper())
        check("maximum 5 minutes (hidden and shown), fade 10 s",
              win.now_playing_periodic_hidden_seconds == 300 and win.now_playing_periodic_shown_seconds == 300 and win.now_playing_periodic_fade_duration == 10.0,
              f"{win.now_playing_periodic_hidden_seconds} {win.now_playing_periodic_shown_seconds} {win.now_playing_periodic_fade_duration}")
        h.set_value(math.sqrt(60)); check("about the middle of the slider is ~1 minute", win.now_playing_periodic_hidden_seconds == 60 and abs(math.sqrt(60) / math.sqrt(300) - 0.45) < 0.01)
        h.set_value(math.sqrt(7)); check("fine steps at the short end (7 s)", win.now_playing_periodic_hidden_seconds == 7)
        win.save_settings()
        d = json.load(open(f"{OUT}/config/melange/settings.json"))
        check("new files are version 2", d.get("version") == 2)
    elif PHASE == "reopen":
        check("version 2 file reopens at the same times (7 s / 5 min)", win.now_playing_periodic_hidden_seconds == 7 and win.now_playing_periodic_shown_seconds == 300,
              f"{win.now_playing_periodic_hidden_seconds} {win.now_playing_periodic_shown_seconds}")
    else:
        check("old saved 24 s / 30 s stay 24 s / 30 s", win.now_playing_periodic_hidden_seconds == 24 and win.now_playing_periodic_shown_seconds == 30,
              f"{win.now_playing_periodic_hidden_seconds} {win.now_playing_periodic_shown_seconds}")
        check("old fade 5 s kept", win.now_playing_periodic_fade_duration == 5.0)
    print("SUMMARY", f"{sum(results)}/{len(results)} passed", flush=True); os._exit(0)
app.connect_after("activate", lambda a: GLib.timeout_add(500, run, a.get_active_window()))
app.run([])
