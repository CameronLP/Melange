# projectm_view.py
#
# Copyright 2026 Cameron
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Melange's visualizer: native MilkDrop rendering with libprojectM in a
Gtk.GLArea.

Replaced the earlier Butterchurn/WebKitGTK renderer. See
docs/projectm-exploration.md for the background and the integration
problems worked around below.
"""

import array
import ctypes
import locale
import os
import zipfile
from pathlib import Path
from typing import NamedTuple

from gi.repository import Gtk, GLib

# projectM's "Cream of the Crop" collection (Category/Subfolder/*.milk)
# and the MilkDrop texture pack, both installed by flatpak modules into
# pkgdatadir next to this module's own directory (see
# com.cameronlp.Melange.json). The presets ship as one zip
# (flatpak/pack_presets.py: ~30 MB instead of ~131 MB of loose files)
# and are loaded straight from it. MELANGE_PROJECTM_PRESETS can point
# at a .zip or a plain folder instead, e.g. when running from a source
# tree.
PRESET_SOURCE = Path(os.environ.get(
    "MELANGE_PROJECTM_PRESETS",
    Path(__file__).resolve().parent.parent / "projectm-presets.zip"
))
TEXTURE_DIR = Path(os.environ.get(
    "MELANGE_PROJECTM_TEXTURES",
    Path(__file__).resolve().parent.parent / "projectm-textures"
))

LIBRARY_NAME = os.environ.get("MELANGE_PROJECTM_LIB", "libprojectM-4.so.4")

PROJECTM_STEREO = 2
GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_BUFFER_BIT = 0x4000
GL_READ_FRAMEBUFFER = 0x8CA8
GL_DRAW_FRAMEBUFFER = 0x8CA9
GL_TEXTURE_2D = 0x0DE1
GL_RGBA8 = 0x8058
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_LINEAR = 0x2601
GL_COLOR_ATTACHMENT0 = 0x8CE0

# Used as projectM's own preset duration while beat cuts are on: it then
# never asks for a timed switch, only beat-triggered ones (Melange's
# PresetManager does the timed cycling).
NEVER_SECONDS = 1.0e9

# At most this much captured audio waits for the next frame (0.25s of
# 44.1kHz stereo S16) - same bound as window.py's MAX_PENDING_AUDIO_BYTES.
MAX_PENDING_AUDIO_BYTES = 44100

SMOOTH_TRANSITION_SECONDS = 2.0

_PresetSwitchRequestedCallback = ctypes.CFUNCTYPE(None, ctypes.c_bool, ctypes.c_void_p)

_PresetFailedCallback = ctypes.CFUNCTYPE(
    None, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p
)

_lib = None
_gl = None
_load_error = None


def _load_libraries():

    global _lib, _gl, _load_error

    if _lib is not None or _load_error is not None:
        return _lib is not None

    try:
        lib = ctypes.CDLL(LIBRARY_NAME)
        gl = ctypes.CDLL("libGL.so.1")
    except OSError as error:
        _load_error = str(error)
        return False

    lib.projectm_create.restype = ctypes.c_void_p
    lib.projectm_destroy.argtypes = [ctypes.c_void_p]
    lib.projectm_set_window_size.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t
    ]
    lib.projectm_load_preset_file.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool
    ]
    lib.projectm_load_preset_data.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool
    ]
    lib.projectm_pcm_add_int16.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int
    ]
    lib.projectm_set_preset_locked.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    lib.projectm_set_soft_cut_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
    lib.projectm_set_hard_cut_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
    lib.projectm_set_hard_cut_enabled.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    lib.projectm_set_hard_cut_sensitivity.argtypes = [ctypes.c_void_p, ctypes.c_float]
    lib.projectm_set_preset_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
    lib.projectm_set_mesh_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    lib.projectm_set_fps.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    lib.projectm_set_preset_switch_requested_event_callback.argtypes = [
        ctypes.c_void_p, _PresetSwitchRequestedCallback, ctypes.c_void_p
    ]
    lib.projectm_set_texture_search_paths.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t
    ]
    lib.projectm_set_preset_switch_failed_event_callback.argtypes = [
        ctypes.c_void_p, _PresetFailedCallback, ctypes.c_void_p
    ]
    lib.projectm_opengl_render_frame.argtypes = [ctypes.c_void_p]

    if hasattr(lib, "projectm_opengl_render_frame_fbo"):
        lib.projectm_opengl_render_frame_fbo.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32
        ]

    gl.glGetIntegerv.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
    gl.glBindFramebuffer.argtypes = [ctypes.c_uint, ctypes.c_uint]
    gl.glColorMask.argtypes = [ctypes.c_ubyte] * 4
    gl.glClearColor.argtypes = [ctypes.c_float] * 4
    gl.glClear.argtypes = [ctypes.c_uint]
    gl.glGenFramebuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    gl.glDeleteFramebuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    gl.glGenTextures.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    gl.glDeleteTextures.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    gl.glBindTexture.argtypes = [ctypes.c_uint, ctypes.c_uint]
    gl.glTexImage2D.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
    ]
    gl.glTexParameteri.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    gl.glFramebufferTexture2D.argtypes = [
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int
    ]
    gl.glBlitFramebuffer.argtypes = [ctypes.c_int] * 8 + [ctypes.c_uint, ctypes.c_uint]

    _lib, _gl = lib, gl

    return True


def availability():
    """(available, reason) - whether libprojectM and presets can be used."""

    if not _load_libraries():
        return False, f"libprojectM couldn't be loaded ({_load_error})"

    if not find_presets():
        return False, f"No .milk presets found in {PRESET_SOURCE}"

    return True, ""


class ZipPreset(NamedTuple):
    """A preset inside the bundled zip, by its member path
    (e.g. "Dancer/Whirl/name.milk")."""

    member: str

    @property
    def stem(self):

        return Path(self.member).stem


_archive = None


def _open_archive():

    global _archive

    if _archive is None:
        _archive = zipfile.ZipFile(PRESET_SOURCE)

    return _archive


def _is_shown(parts):

    # Folders starting with "!" (e.g. "! Transition") hold presets meant
    # for transitions, not for showing on their own.
    return not any(part.startswith("!") for part in parts)


def find_presets():
    """Every bundled preset: ZipPreset entries when PRESET_SOURCE is a
    zip, Paths when it's a folder."""

    if PRESET_SOURCE.is_file():
        try:
            names = _open_archive().namelist()
        except (OSError, zipfile.BadZipFile) as error:
            print("projectM: couldn't open preset archive:", error)
            return []

        return sorted(
            ZipPreset(name) for name in names
            if name.endswith(".milk") and _is_shown(Path(name).parts)
        )

    if PRESET_SOURCE.is_dir():
        return sorted(
            path for path in PRESET_SOURCE.rglob("*.milk")
            if _is_shown(path.relative_to(PRESET_SOURCE).parts)
        )

    return []


def preset_catalog():
    """{display name: preset} for the bundled presets (ZipPreset or
    Path). Names are file stems (unique across the whole Cream of the
    Crop collection); should a stem ever repeat, its folder path is
    appended to keep names unique."""

    presets = find_presets()
    stems = {}

    for preset in presets:
        stems[preset.stem] = stems.get(preset.stem, 0) + 1

    catalog = {}

    for preset in presets:
        name = preset.stem

        if stems[name] > 1:
            name = f"{name} ({'/'.join(preset_folders(preset))})"

        catalog[name] = preset

    return catalog


def read_preset_bytes(preset):
    """The .milk file contents of a bundled ZipPreset or a file Path, or
    None if it can't be read."""

    try:
        if isinstance(preset, ZipPreset):
            return _open_archive().read(preset.member)
        return Path(preset).read_bytes()
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def preset_folders(preset):
    """The folder names above a bundled preset, e.g. ("Dancer", "Whirl");
    () for a file loaded from elsewhere."""

    if isinstance(preset, ZipPreset):
        return Path(preset.member).parts[:-1]

    try:
        return Path(preset).relative_to(PRESET_SOURCE).parts[:-1]
    except ValueError:
        return ()


class ProjectMView(Gtk.GLArea):
    """Renders presets it's told to load - navigation, history, the
    queue and cycling live in preset_manager.PresetManager."""

    def __init__(self, on_preset_failed=None):

        super().__init__()

        # on_preset_failed(preset, reason) - called on the main loop with
        # the ZipPreset/Path that was being loaded.
        self.on_preset_failed_callback = on_preset_failed

        self.handle = None
        self.size_known = False
        self.pending_audio = bytearray()
        self.requested_preset = None  # (preset, smooth), loaded on next render
        self.loading_preset = None
        self.transition_seconds = SMOOTH_TRANSITION_SECONDS

        # on_beat() - called on the main loop when beat cuts are on and
        # projectM detects a beat strong enough to change preset.
        self.on_beat = None

        # Settings (Preferences > Audio / Playback / Rendering); applied
        # when the projectM instance exists, see apply_settings.
        self.sensitivity = 1.0
        self.mesh_size = (48, 36)
        self.frame_rate_cap = 0          # 0 = every frame the display shows
        self.render_scale = 1.0
        self.beat_cuts_enabled = False
        self.beat_threshold = 2.0
        self.beat_min_seconds = 10.0

        self.last_frame_time = None
        self.offscreen = None            # (framebuffer, texture, width, height)

        self.set_required_version(3, 3)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.connect("realize", self.on_realize)
        self.connect("unrealize", self.on_unrealize)
        self.connect("render", self.on_render)
        self.connect("resize", self.on_resize)

        self.add_tick_callback(self.on_tick)

    # --- lifecycle ------------------------------------------------------

    def on_realize(self, area):

        # projectM parses .milk numbers with the C library's locale-
        # dependent functions, and GTK switches the process to the
        # user's locale. Under a comma-decimal locale (e.g. nb_NO)
        # every preset value mis-parses and everything renders black,
        # so numeric formatting has to stay "C" while projectM is used.
        locale.setlocale(locale.LC_NUMERIC, "C")

        self.make_current()

        if self.get_error() is not None:
            print("projectM: GL context error:", self.get_error().message)
            return

        self.handle = _lib.projectm_create()

        if not self.handle:
            print("projectM: projectm_create() failed")
            return

        self._failed_callback = _PresetFailedCallback(self.on_preset_failed)
        _lib.projectm_set_preset_switch_failed_event_callback(
            self.handle, self._failed_callback, None
        )
        self._switch_requested_callback = _PresetSwitchRequestedCallback(
            self.on_preset_switch_requested
        )
        _lib.projectm_set_preset_switch_requested_event_callback(
            self.handle, self._switch_requested_callback, None
        )
        _lib.projectm_set_soft_cut_duration(self.handle, self.transition_seconds)

        if TEXTURE_DIR.is_dir():
            paths = (ctypes.c_char_p * 1)(str(TEXTURE_DIR).encode())
            _lib.projectm_set_texture_search_paths(self.handle, paths, 1)

        self.size_known = False
        self.apply_settings()

    def on_unrealize(self, area):

        self.make_current()
        self.free_offscreen()

        if self.handle:
            _lib.projectm_destroy(self.handle)
            self.handle = None

    def on_resize(self, area, width, height):

        if self.handle and self.size_known:
            _lib.projectm_set_window_size(self.handle, *self.render_size(width, height))

    def render_size(self, width, height):

        return (
            max(1, round(width * self.render_scale)),
            max(1, round(height * self.render_scale)),
        )

    def on_tick(self, widget, frame_clock):

        # Frame Rate cap: skip frames until enough time has passed (a
        # little early is fine - frames only come at the display rate).
        if self.frame_rate_cap > 0:
            now = frame_clock.get_frame_time() / 1_000_000

            if (
                self.last_frame_time is not None
                and now - self.last_frame_time < 0.9 / self.frame_rate_cap
            ):
                return GLib.SOURCE_CONTINUE

            self.last_frame_time = now

        self.queue_render()

        return GLib.SOURCE_CONTINUE

    # --- rendering ------------------------------------------------------

    def on_render(self, area, context):

        if not self.handle:
            return True

        scale = self.get_scale_factor()
        width, height = self.get_width() * scale, self.get_height() * scale

        if width <= 0 or height <= 0:
            return True

        # The area is still 0x0 at realize, so presets are only loaded
        # once there's a real size.
        if not self.size_known:
            self.size_known = True
            _lib.projectm_set_window_size(self.handle, *self.render_size(width, height))

        if self.requested_preset is not None:
            preset, smooth = self.requested_preset
            self.requested_preset = None
            self.loading_preset = preset

            if isinstance(preset, ZipPreset):
                try:
                    data = _open_archive().read(preset.member)
                except (OSError, KeyError, zipfile.BadZipFile) as error:
                    self.report_failure(preset, str(error))
                else:
                    _lib.projectm_load_preset_data(self.handle, data, smooth)
            else:
                _lib.projectm_load_preset_file(self.handle, str(preset).encode(), smooth)

        if self.pending_audio:
            data = self.scaled_audio(bytes(self.pending_audio))
            self.pending_audio.clear()
            _lib.projectm_pcm_add_int16(
                self.handle, data, len(data) // 4, PROJECTM_STEREO
            )

        framebuffer = ctypes.c_int(0)
        _gl.glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(framebuffer))

        # Render Scale: projectM draws into a smaller (or larger)
        # offscreen buffer, which is then scaled into the GLArea's.
        if self.render_scale != 1.0:
            target = self.ensure_offscreen(*self.render_size(width, height))
        else:
            self.free_offscreen()
            target = framebuffer.value

        self.render_projectm_into(target)

        if target != framebuffer.value:
            _, _, render_width, render_height = self.offscreen
            _gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, target)
            _gl.glBindFramebuffer(GL_DRAW_FRAMEBUFFER, framebuffer.value)
            _gl.glBlitFramebuffer(
                0, 0, render_width, render_height, 0, 0, width, height,
                GL_COLOR_BUFFER_BIT, GL_LINEAR
            )

        # MilkDrop never used alpha and some presets leave it at 0,
        # which GTK would composite as transparent - force opaque.
        _gl.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer.value)
        _gl.glColorMask(0, 0, 0, 1)
        _gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        _gl.glClear(GL_COLOR_BUFFER_BIT)
        _gl.glColorMask(1, 1, 1, 1)

        return True

    def render_projectm_into(self, framebuffer):

        # projectM 4.1.x always draws to framebuffer 0 unless patched
        # (flatpak/projectm-4.1.8-render-to-bound-fbo.patch makes it use
        # whatever is bound); newer versions take the target explicitly.
        if hasattr(_lib, "projectm_opengl_render_frame_fbo"):
            _lib.projectm_opengl_render_frame_fbo(self.handle, framebuffer)
        else:
            _gl.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer)
            _lib.projectm_opengl_render_frame(self.handle)

    def ensure_offscreen(self, width, height):

        if self.offscreen is not None and self.offscreen[2:] == (width, height):
            return self.offscreen[0]

        self.free_offscreen()

        texture = ctypes.c_uint(0)
        _gl.glGenTextures(1, ctypes.byref(texture))
        _gl.glBindTexture(GL_TEXTURE_2D, texture.value)
        _gl.glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0,
            GL_RGBA, GL_UNSIGNED_BYTE, None
        )
        _gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        _gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        _gl.glBindTexture(GL_TEXTURE_2D, 0)

        framebuffer = ctypes.c_uint(0)
        _gl.glGenFramebuffers(1, ctypes.byref(framebuffer))
        _gl.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer.value)
        _gl.glFramebufferTexture2D(
            GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture.value, 0
        )

        self.offscreen = (framebuffer.value, texture.value, width, height)

        return framebuffer.value

    def free_offscreen(self):

        if self.offscreen is None:
            return

        framebuffer, texture, _, _ = self.offscreen
        _gl.glDeleteFramebuffers(1, ctypes.byref(ctypes.c_uint(framebuffer)))
        _gl.glDeleteTextures(1, ctypes.byref(ctypes.c_uint(texture)))
        self.offscreen = None

    # --- settings -------------------------------------------------------

    def apply_settings(self):

        if not self.handle:
            return

        _lib.projectm_set_mesh_size(self.handle, *self.mesh_size)
        _lib.projectm_set_fps(self.handle, self.frame_rate_cap or 60)
        _lib.projectm_set_soft_cut_duration(self.handle, self.transition_seconds)

        if self.size_known:
            scale = self.get_scale_factor()
            _lib.projectm_set_window_size(
                self.handle,
                *self.render_size(self.get_width() * scale, self.get_height() * scale)
            )

        # Beat cuts: projectM asks for a switch (on_preset_switch_
        # requested) when the volume jumps by more than the threshold
        # and the preset has shown for at least beat_min_seconds. With
        # them off, projectM is locked and never asks - Melange's
        # PresetManager does all switching.
        if self.beat_cuts_enabled:
            _lib.projectm_set_preset_duration(self.handle, NEVER_SECONDS)
            _lib.projectm_set_hard_cut_sensitivity(self.handle, self.beat_threshold)
            _lib.projectm_set_hard_cut_duration(self.handle, self.beat_min_seconds)
            _lib.projectm_set_hard_cut_enabled(self.handle, True)
            _lib.projectm_set_preset_locked(self.handle, False)
        else:
            _lib.projectm_set_hard_cut_enabled(self.handle, False)
            _lib.projectm_set_preset_locked(self.handle, True)

    def set_sensitivity(self, gain):

        self.sensitivity = max(0.0, float(gain))

    def set_mesh_size(self, width):

        # Same 4:3 shape the Mesh Size setting always used.
        width = max(8, int(width))
        self.mesh_size = (width, max(6, round(width * 0.75)))
        self.apply_settings()

    def set_frame_rate_cap(self, fps):

        self.frame_rate_cap = max(0, int(fps))
        self.last_frame_time = None
        self.apply_settings()

    def set_render_scale(self, scale):

        self.render_scale = max(0.1, float(scale))
        self.apply_settings()
        self.queue_render()

    def set_beat_cuts(self, enabled=None, threshold=None, min_seconds=None):

        if enabled is not None:
            self.beat_cuts_enabled = bool(enabled)
        if threshold is not None:
            self.beat_threshold = float(threshold)
        if min_seconds is not None:
            self.beat_min_seconds = float(min_seconds)

        self.apply_settings()

    def on_preset_switch_requested(self, is_hard_cut, user_data):

        # Only beat (hard) cuts are expected - timed switching is set to
        # never happen. Called from inside the render call.
        if is_hard_cut and self.on_beat:
            GLib.idle_add(lambda: (self.on_beat(), False)[1])

    def scaled_audio(self, data):

        # Sensitivity: scale the S16 samples before projectM sees them.
        if self.sensitivity == 1.0:
            return data

        samples = array.array("h")
        samples.frombytes(data[:len(data) - len(data) % 2])
        gain = self.sensitivity

        return array.array("h", (
            max(-32768, min(32767, int(sample * gain))) for sample in samples
        )).tobytes()

    # --- audio ----------------------------------------------------------

    def push_audio(self, data):

        # Interleaved S16LE stereo, called on the main thread
        # (window.py flush_pending_audio); consumed on the next frame.
        self.pending_audio.extend(data)

        excess = len(self.pending_audio) - MAX_PENDING_AUDIO_BYTES

        if excess > 0:
            del self.pending_audio[:excess + (-excess % 4)]

    # --- presets --------------------------------------------------------

    def load_preset(self, preset, smooth=True):
        """Show this preset - a ZipPreset from the bundled collection or
        a .milk file Path (loaded on the next frame, when the GL context
        is current)."""

        if not isinstance(preset, ZipPreset):
            preset = Path(preset)

        self.requested_preset = (preset, smooth)
        self.queue_render()

    def set_transition_seconds(self, seconds):

        self.transition_seconds = max(0.0, float(seconds))
        self.apply_settings()

    def on_preset_failed(self, filename, message, user_data):

        reason = message.decode(errors="replace") if message else ""

        # projectM only knows a filename for file loads, so report the
        # preset this view was loading instead.
        self.report_failure(self.loading_preset, reason)

    def report_failure(self, preset, reason):

        print(f"projectM: preset failed: {preset}: {reason}")

        # May be called from inside projectM's render call; report it
        # once that has returned.
        if self.on_preset_failed_callback:
            GLib.idle_add(
                lambda: (self.on_preset_failed_callback(preset, reason), False)[1]
            )
