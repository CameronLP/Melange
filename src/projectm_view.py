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

"""Experimental native MilkDrop renderer: libprojectM in a Gtk.GLArea.

An alternative to the Butterchurn/WebKitGTK visualizer, selected with
the "projectM Renderer" switch in Preferences > Experimental. See
docs/projectm-exploration.md for the background and the integration
problems worked around below.
"""

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

# At most this much captured audio waits for the next frame (0.25s of
# 44.1kHz stereo S16) - same bound as window.py's MAX_PENDING_AUDIO_BYTES.
MAX_PENDING_AUDIO_BYTES = 44100

SMOOTH_TRANSITION_SECONDS = 2.0

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
        _lib.projectm_set_soft_cut_duration(self.handle, self.transition_seconds)

        if TEXTURE_DIR.is_dir():
            paths = (ctypes.c_char_p * 1)(str(TEXTURE_DIR).encode())
            _lib.projectm_set_texture_search_paths(self.handle, paths, 1)

        # Melange decides when to switch presets (cycle interval,
        # next/previous), not projectM's own timer/hard cuts.
        _lib.projectm_set_preset_locked(self.handle, True)

        self.size_known = False

    def on_unrealize(self, area):

        self.make_current()

        if self.handle:
            _lib.projectm_destroy(self.handle)
            self.handle = None

    def on_resize(self, area, width, height):

        if self.handle and self.size_known:
            _lib.projectm_set_window_size(self.handle, width, height)

    def on_tick(self, widget, frame_clock):

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
            _lib.projectm_set_window_size(self.handle, width, height)

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
            data = bytes(self.pending_audio)
            self.pending_audio.clear()
            _lib.projectm_pcm_add_int16(
                self.handle, data, len(data) // 4, PROJECTM_STEREO
            )

        framebuffer = ctypes.c_int(0)
        _gl.glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(framebuffer))

        # projectM 4.1.x always draws to framebuffer 0 unless patched
        # (flatpak/projectm-4.1.8-render-to-bound-fbo.patch makes it use
        # whatever is bound - the GLArea's); newer versions take the
        # target explicitly.
        if hasattr(_lib, "projectm_opengl_render_frame_fbo"):
            _lib.projectm_opengl_render_frame_fbo(self.handle, framebuffer.value)
        else:
            _lib.projectm_opengl_render_frame(self.handle)

        # MilkDrop never used alpha and some presets leave it at 0,
        # which GTK would composite as transparent - force opaque.
        _gl.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer.value)
        _gl.glColorMask(0, 0, 0, 1)
        _gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        _gl.glClear(GL_COLOR_BUFFER_BIT)
        _gl.glColorMask(1, 1, 1, 1)

        return True

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

        if self.handle:
            _lib.projectm_set_soft_cut_duration(self.handle, self.transition_seconds)

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
