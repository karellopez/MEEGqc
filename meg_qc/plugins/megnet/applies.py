"""Which recordings MEGnet applies to.

MEGnet classifies ICA components from topographic maps drawn on the
magnetometer (or axial-gradiometer) layout of the systems it was trained on.
The megnet-neuro package itself checks none of this: on an OPM or any other
system with magnetometer-typed channels it returns a result with no warning,
and on a gradiometer-only system it fails inside its plotting code. So the
plugin checks first, and says why it does not run.

Sources (2026-11): Treacher et al., NeuroImage 2021 (CTF and 4D training
data); the megnet-neuro README ("retrained using repository data from MEGIN,
CTF, 4D, and KIT systems"); megnet-neuro 0.3.6 ``prep_inputs/ICA.py`` (picks
``meg='mag'`` channels, resamples to 250 Hz) and ``megnet_utilities.py`` (60 s
windows with 15 s overlap; the voting step needs at least two windows, so
105 s).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: The voting step needs two 60 s windows that overlap by 15 s.
MIN_SECONDS = 105.0
#: MEGnet resamples to 250 Hz; less than that is upsampled data it never saw.
MIN_SFREQ = 250.0
#: MEGnet fits 20 ICA components.
MIN_MAG_CHANNELS = 20

# FIFF coil types (mne.io.constants.FIFF), as plain numbers so this module
# works without importing MNE's constants.
_CTF_AXIAL = 5001        # FIFFV_COIL_CTF_GRAD
_MAGNES_MAG = 4001       # FIFFV_COIL_MAGNES_MAG (4D/BTi)
_KIT_AXIAL = 6001        # FIFFV_COIL_KIT_GRAD
_MEGIN_MAGS = {3021, 3022, 3023, 3024, 3025}  # FIFFV_COIL_VV_MAG_*

SYSTEMS = "MEGIN (Vectorview, TRIUX), CTF, 4D/BTi and KIT"


def system_of(coil_type: int) -> Optional[str]:
    """The trained system a coil type belongs to, or ``None``."""
    coil = int(coil_type) & 0xFFFF
    if coil // 1000 == 3:
        return "MEGIN"
    return {_CTF_AXIAL: "CTF", _MAGNES_MAG: "4D", _KIT_AXIAL: "KIT"}.get(coil)


def why_not(raw) -> Optional[str]:
    """``None`` when MEGnet can run on ``raw``, else the reason it cannot."""
    import mne

    info = raw.info
    meg = mne.pick_types(info, meg=True, ref_meg=False, exclude="bads")
    if len(meg) == 0:
        return "it needs MEG channels and this recording has none"

    systems = {system_of(info["chs"][i]["coil_type"]) for i in meg}
    if None in systems:
        unknown = sorted({int(info["chs"][i]["coil_type"]) & 0xFFFF for i in meg
                          if system_of(info["chs"][i]["coil_type"]) is None})
        return (f"it was trained on {SYSTEMS} recordings, and this recording's MEG "
                f"sensors are of another kind (coil type {', '.join(map(str, unknown))})")
    if len(systems) > 1:
        return f"this recording mixes MEG sensors of several systems ({', '.join(sorted(systems))})"
    if systems == {"MEGIN"} and not any(
            int(info["chs"][i]["coil_type"]) & 0xFFFF in _MEGIN_MAGS for i in meg):
        return "it needs the magnetometers of a MEGIN system and this recording has none"

    mags = mne.pick_types(info, meg="mag", ref_meg=False, exclude="bads")
    if len(mags) < MIN_MAG_CHANNELS:
        return (f"it needs at least {MIN_MAG_CHANNELS} good magnetometer channels; "
                f"this recording has {len(mags)}")
    locs = np.array([info["chs"][i]["loc"][:3] for i in mags], dtype=float)
    if not np.all(np.isfinite(locs)) or np.any(np.all(locs == 0, axis=1)):
        return "it needs sensor positions and some of this recording's are missing"

    sfreq = float(info["sfreq"])
    if sfreq < MIN_SFREQ:
        return f"it needs a sampling rate of at least {MIN_SFREQ:.0f} Hz; this recording has {sfreq:g} Hz"
    seconds = raw.n_times / sfreq
    if seconds < MIN_SECONDS:
        return (f"it needs at least {MIN_SECONDS:.0f} s of data (two 60 s windows "
                f"overlapping by 15 s); this recording is {seconds:.1f} s")
    return None
