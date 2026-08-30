# projectm.py
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

# ctypes bindings for libprojectM 4.x (core rendering) and its
# companion libprojectM-4-playlist (preset list/shuffle/switching).
# There are no Python bindings or GObject-Introspection for either
# library upstream, so this hand-binds the subset of the C API Melange
# actually uses. Signatures below were confirmed against the real
# installed headers, not guessed - see the projectm-spike work log for
# how each one was verified.
#
# The tagged v4.1.4 release hardcodes rendering to framebuffer 0
# (glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0) in ProjectM::RenderFrame),
# which is incompatible with Gtk.GLArea (always renders into its own
# offscreen FBO, never FBO 0) - Melange's flatpak module pins to commit
# bd2b1ba9 or later, which adds projectm_opengl_render_frame_fbo() for
# exactly this case.

import ctypes


_lib = ctypes.CDLL("libprojectM-4.so")
_playlist_lib = ctypes.CDLL("libprojectM-4-playlist.so")

PROJECTM_MONO = 1
PROJECTM_STEREO = 2

_lib.projectm_create.restype = ctypes.c_void_p
_lib.projectm_create.argtypes = []

_lib.projectm_destroy.argtypes = [ctypes.c_void_p]
_lib.projectm_destroy.restype = None

_lib.projectm_load_preset_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]
_lib.projectm_load_preset_file.restype = None

_lib.projectm_set_window_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
_lib.projectm_set_window_size.restype = None

_lib.projectm_opengl_render_frame_fbo.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_lib.projectm_opengl_render_frame_fbo.restype = None

_lib.projectm_pcm_add_int16.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_int16),
    ctypes.c_uint,
    ctypes.c_int,
]
_lib.projectm_pcm_add_int16.restype = None

_lib.projectm_set_beat_sensitivity.argtypes = [ctypes.c_void_p, ctypes.c_float]
_lib.projectm_set_beat_sensitivity.restype = None

_lib.projectm_set_preset_locked.argtypes = [ctypes.c_void_p, ctypes.c_bool]
_lib.projectm_set_preset_locked.restype = None

_lib.projectm_set_preset_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
_lib.projectm_set_preset_duration.restype = None

_lib.projectm_get_preset_duration.argtypes = [ctypes.c_void_p]
_lib.projectm_get_preset_duration.restype = ctypes.c_double


class ProjectM:
    """A single projectM rendering instance. Must be created with a
    real, current OpenGL context bound (see ProjectMView.on_realize) -
    forcing desktop GL (not GLES) is required, since this library is
    built without GLES support."""

    def __init__(self):

        self.handle = _lib.projectm_create()

        if not self.handle:
            raise RuntimeError(
                "projectm_create() returned NULL - most likely the "
                "current GL context is GLES rather than desktop GL"
            )

    def destroy(self):

        if self.handle:
            _lib.projectm_destroy(self.handle)
            self.handle = None

    def set_window_size(self, width, height):

        _lib.projectm_set_window_size(self.handle, width, height)

    def render_frame(self, fbo):

        _lib.projectm_opengl_render_frame_fbo(self.handle, fbo)

    def load_preset_file(self, path, smooth_transition=True):

        _lib.projectm_load_preset_file(
            self.handle,
            str(path).encode("utf-8"),
            smooth_transition,
        )

    def pcm_add_int16(self, samples, channels=PROJECTM_STEREO):
        """samples: a ctypes (c_int16 * n) array, interleaved if stereo."""

        count = len(samples) // channels

        _lib.projectm_pcm_add_int16(self.handle, samples, count, channels)

    def set_beat_sensitivity(self, sensitivity):

        _lib.projectm_set_beat_sensitivity(self.handle, sensitivity)

    def set_preset_locked(self, locked):

        _lib.projectm_set_preset_locked(self.handle, locked)

    def set_preset_duration(self, seconds):
        """seconds <= 0 disables automatic preset advancement."""

        _lib.projectm_set_preset_duration(self.handle, seconds)

    def get_preset_duration(self):

        return _lib.projectm_get_preset_duration(self.handle)


_playlist_lib.projectm_playlist_create.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_create.restype = ctypes.c_void_p

_playlist_lib.projectm_playlist_destroy.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_destroy.restype = None

_playlist_lib.projectm_playlist_size.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_size.restype = ctypes.c_uint32

_playlist_lib.projectm_playlist_clear.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_clear.restype = None

_playlist_lib.projectm_playlist_items.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
_playlist_lib.projectm_playlist_items.restype = ctypes.POINTER(ctypes.c_char_p)

_playlist_lib.projectm_playlist_item.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_playlist_lib.projectm_playlist_item.restype = ctypes.c_void_p

_playlist_lib.projectm_playlist_free_string.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_free_string.restype = None

_playlist_lib.projectm_playlist_free_string_array.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
_playlist_lib.projectm_playlist_free_string_array.restype = None

_playlist_lib.projectm_playlist_add_path.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool, ctypes.c_bool
]
_playlist_lib.projectm_playlist_add_path.restype = ctypes.c_uint32

_playlist_lib.projectm_playlist_add_preset.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool
]
_playlist_lib.projectm_playlist_add_preset.restype = ctypes.c_bool

_playlist_lib.projectm_playlist_set_shuffle.argtypes = [ctypes.c_void_p, ctypes.c_bool]
_playlist_lib.projectm_playlist_set_shuffle.restype = None

_playlist_lib.projectm_playlist_get_shuffle.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_get_shuffle.restype = ctypes.c_bool

_playlist_lib.projectm_playlist_set_position.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_bool]
_playlist_lib.projectm_playlist_set_position.restype = ctypes.c_uint32

_playlist_lib.projectm_playlist_get_position.argtypes = [ctypes.c_void_p]
_playlist_lib.projectm_playlist_get_position.restype = ctypes.c_uint32

_playlist_lib.projectm_playlist_play_next.argtypes = [ctypes.c_void_p, ctypes.c_bool]
_playlist_lib.projectm_playlist_play_next.restype = ctypes.c_uint32

_playlist_lib.projectm_playlist_play_previous.argtypes = [ctypes.c_void_p, ctypes.c_bool]
_playlist_lib.projectm_playlist_play_previous.restype = ctypes.c_uint32

PROJECTM_PLAYLIST_SWITCHED_EVENT = ctypes.CFUNCTYPE(
    None, ctypes.c_bool, ctypes.c_uint32, ctypes.c_void_p
)

_playlist_lib.projectm_playlist_set_preset_switched_event_callback.argtypes = [
    ctypes.c_void_p, PROJECTM_PLAYLIST_SWITCHED_EVENT, ctypes.c_void_p
]
_playlist_lib.projectm_playlist_set_preset_switched_event_callback.restype = None


class Playlist:
    """Preset list/shuffle/switching, connected to a ProjectM instance.
    Switching presets through this (play_next/play_previous/
    set_position) is what actually tells the connected ProjectM
    instance to load the new preset - there's no separate load call
    needed."""

    def __init__(self, projectm):

        self.handle = _playlist_lib.projectm_playlist_create(projectm.handle)

        # Keeps the ctypes closure (and therefore the C function
        # pointer it wraps) alive for as long as this Playlist exists -
        # without this reference, it could be garbage collected while
        # libprojectM still holds the raw pointer, corrupting later
        # calls into it.
        self._switched_callback = None

    def destroy(self):

        if self.handle:
            _playlist_lib.projectm_playlist_destroy(self.handle)
            self.handle = None

    def add_path(self, path, recurse_subdirs=True, allow_duplicates=False):

        return _playlist_lib.projectm_playlist_add_path(
            self.handle,
            str(path).encode("utf-8"),
            recurse_subdirs,
            allow_duplicates,
        )

    def add_preset(self, path, allow_duplicates=True):

        return _playlist_lib.projectm_playlist_add_preset(
            self.handle,
            str(path).encode("utf-8"),
            allow_duplicates,
        )

    def size(self):

        return _playlist_lib.projectm_playlist_size(self.handle)

    def clear(self):

        _playlist_lib.projectm_playlist_clear(self.handle)

    def items(self, start=0, count=None):

        if count is None:
            count = self.size()

        raw = _playlist_lib.projectm_playlist_items(self.handle, start, count)

        result = []
        i = 0

        while raw[i] is not None:
            result.append(raw[i].decode("utf-8"))
            i += 1

        _playlist_lib.projectm_playlist_free_string_array(raw)

        return result

    def item(self, index):

        ptr = _playlist_lib.projectm_playlist_item(self.handle, index)

        if not ptr:
            return None

        value = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")

        _playlist_lib.projectm_playlist_free_string(ptr)

        return value

    def set_shuffle(self, shuffle):

        _playlist_lib.projectm_playlist_set_shuffle(self.handle, shuffle)

    def get_shuffle(self):

        return _playlist_lib.projectm_playlist_get_shuffle(self.handle)

    def set_position(self, index, hard_cut=False):

        return _playlist_lib.projectm_playlist_set_position(self.handle, index, hard_cut)

    def get_position(self):

        return _playlist_lib.projectm_playlist_get_position(self.handle)

    def play_next(self, hard_cut=False):

        return _playlist_lib.projectm_playlist_play_next(self.handle, hard_cut)

    def play_previous(self, hard_cut=False):

        return _playlist_lib.projectm_playlist_play_previous(self.handle, hard_cut)

    def set_switched_callback(self, callback):
        """callback(is_hard_cut: bool, index: int) -> None, or None to
        clear it."""

        if callback is None:
            self._switched_callback = None

            _playlist_lib.projectm_playlist_set_preset_switched_event_callback(
                self.handle, ctypes.cast(None, PROJECTM_PLAYLIST_SWITCHED_EVENT), None
            )

            return

        def _trampoline(is_hard_cut, index, user_data):
            callback(is_hard_cut, index)

        self._switched_callback = PROJECTM_PLAYLIST_SWITCHED_EVENT(_trampoline)

        _playlist_lib.projectm_playlist_set_preset_switched_event_callback(
            self.handle, self._switched_callback, None
        )
