"""Pack projectM's preset collection into one zip for Melange.

Usage: python3 pack_presets.py <collection dir> <output .zip>

9,791 small .milk files take ~131 MB installed; as a zip (each preset
compressed on its own, so any one can be read directly) it's ~30 MB.
src/projectm_view.py loads presets from it with projectm_load_preset_data.
Folders starting with "!" (transition-only presets) are left out.
"""

import sys
import zipfile
from pathlib import Path

source, output = Path(sys.argv[1]), Path(sys.argv[2])

presets = sorted(
    path for path in source.rglob("*.milk")
    if not any(part.startswith("!") for part in path.relative_to(source).parts)
)

output.parent.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in presets:
        # Fixed timestamp: reproducible output, and some presets carry
        # pre-1980 mtimes that zip can't store.
        info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (2000, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, path.read_bytes(), compresslevel=9)

print(f"Packed {len(presets)} presets into {output} ({output.stat().st_size / 1048576:.1f} MB)")
