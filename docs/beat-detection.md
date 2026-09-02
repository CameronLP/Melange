# Beat-driven auto-cycle: two beat modes plus drop detection

**Status: experimental.** Every claim of correctness in this document
(the tempo estimator's BPM accuracy, the "cut false positives by
roughly half to two-thirds" figures, the drop detector's clean
single-detection result) comes from offline testing against
*synthetic* signals in Node - clean click tracks and hand-built
groove/buildup/drop energy curves, not real, varied music played
through the actual live pipeline. It's a reasonable starting point,
not a validated feature. Development on this is paused for now
(see TODO.md) - pick it back up by actually listening across a range
of real tracks/genres and tuning from there, rather than assuming the
synthetic results generalize.

`src/web/main.js` — `checkBeat()` dispatches to one of two independent
beat detectors, selected by the "Beat Detection Mode" dropdown in
Preferences → Playback → Beat Detection, and *also* always runs
`checkDrop()` alongside whichever mode is selected. Beat detection and
drop detection feed separate debug-channel messages -
`BEAT_NAV_NEXT` and `DROP_NAV_NEXT` respectively (both routed to
`next_preset()` in `window.py`, each with its own toast so it's
visible which one fired, distinct from a manual click or a Cycle
Interval tick).

## Mode 1: Energy Threshold (`checkBeatEnergy`)

The original, simpler detector. Sums byte-frequency-domain energy
across the low ~8 bins of a 256-point FFT (roughly 0–1.4kHz at a
44.1kHz context), keeps a rolling ~1s average of that value, and
treats a sample as a beat when it's a configurable multiple above that
average (Beat Sensitivity) and above an absolute floor (Beat Silence
Floor).

**Works well for:** a normal playlist - individual songs starting from
silence, quieter verses against louder choruses. There's real
loud/quiet contrast for the average to compare against.

**Breaks down on:** continuously mixed audio (a DJ set, an uninterrupted
four-on-the-floor stretch). A relentless, evenly-loud kick on every
beat gives the rolling average nothing to contrast against - it just
rises to match the constant level, and nothing looks like an "onset"
relative to it. This was reported directly and is the reason Mode 2
exists.

## Mode 2: Tempo Tracking (`checkBeatTempo`)

A lightweight, real-time simplification of the standard MIR (music
information retrieval) pipeline: **spectral flux onset detection**
feeding **autocorrelation-based periodicity estimation**. Not a literal
implementation of any single paper - see References below for what
it's inspired by and where it diverges.

### Why spectral flux instead of raw energy

`computeSpectralFlux()` sums the *positive-only* frame-to-frame
increase across the low 32 FFT bins (`beatFreqData[i] -
previousMagnitudes[i]`, only counted when positive). This is the key
fix for the continuous-mix case: flux measures the **rise**, not the
absolute level. A kick drum has a sharp attack transient regardless of
how loud the surrounding music already is, so a string of
equally-loud kicks still produces a distinct flux spike at each
attack, even though raw energy never dips in between and an
energy-vs-average test would see nothing.

### Why autocorrelation on top of that

Flux spikes alone would still fire on a hi-hat, a vocal consonant, or
any other transient - not just the beat. `estimateTempoPeriodMs()`
resamples the last 8 seconds of flux history onto a uniform 20ms grid
and autocorrelates it across the lag range corresponding to 60–180
BPM, picking the lag with the strongest self-similarity. That lag is
the estimated beat period, recomputed at most once a second. Once a
period estimate exists, `checkBeatTempo()` uses it to set a minimum
gap between triggers (`max(Beat Cooldown, period * 0.5)`), so a
stray non-beat transient can't fire faster than roughly every half
beat even if it individually passes the local-peak check.

### A real bug found and fixed while building this

Initial testing (`estimateTempoPeriodMs` against a synthetic 128 BPM
click track) locked onto **63 BPM instead of 128** - autocorrelation is
well known to be biased toward tempo *octaves* (a signal at period P
also correlates strongly at period 2P, since every other beat still
lines up). Two fixes, both verified against synthetic 90/120/128/174
BPM test signals afterward (all landed within ~1-2 BPM, which is the
expected quantization error at a 20ms grid):

1. **Finer resample grid** (50ms → 20ms) - the coarser grid was losing
   enough precision on non-round periods (e.g. 468.75ms at 128 BPM)
   that the doubled period actually aligned *better* with the
   quantization grid than the true one did.
2. **Octave-error correction** - after finding the best-scoring lag,
   check whether half that lag (if still in the valid BPM range)
   scores within 60% of the best. If so, prefer the shorter period -
   the standard practical fix for this class of algorithm.

A second, more serious bug surfaced in actual use (not caught by the
synthetic-signal testing above, which used a clean click track rather
than continuous noisy flux): real logs showed **~1375 onset detections
for only 58 actual advances**, with new advances firing as often as
every ~0.3-0.5s regardless of the configured cooldown - the local-peak
test was passing on nearly every frame, so the cooldown timer alone
was pacing triggers rather than genuine beat detection. Root cause:
the comparison average was computed *after* pushing the current
sample into `fluxHistory`, so the sample was being compared partly
against itself - with a short window this made the threshold nearly
meaningless. Fixed by:

1. Computing the local average from history as it stood *before* the
   current sample, not after.
2. Requiring the sample be a genuine causal local maximum over the
   last 5 frames (`RECENT_PEAK_WINDOW`), not just "above average."
3. A 100ms refractory period between accepted onsets, independent of
   the cooldown that paces actual *advances* - even at 180 BPM, beats
   are ~333ms apart, so nothing legitimate needs onsets closer than
   that.

Verified with a synthetic four-on-the-floor signal at several noise
levels (offline harness, not the live app) comparing onset counts
before/after against ~17 true beats in an 8s window:

| noise level | before | after |
|---|---|---|
| low | 30 | 18 |
| moderate | 32 | 18 |
| high | 87 | 37 |
| very high | 134 | 40 |

Better across the board, though clearly not perfect at high noise
levels - a real limitation of this lightweight approach, not fully
resolved.

### Computational cost

Measured with Node (`process.hrtime`), not just estimated:

- `computeSpectralFlux()`: 32 subtractions/comparisons per call, run
  every animation frame (~60Hz) - trivially cheap, far less work than
  a single WebGL draw call the visualizer already does every frame.
- `estimateTempoPeriodMs()`: 8s of history resampled to 400 points at
  a 20ms grid, autocorrelated across a ~33-lag range (60-180 BPM) =
  ~13,200 multiply-adds per call. Measured at **~0.07ms per call**,
  run at most once per second (`TEMPO_ESTIMATE_INTERVAL_MS`) - on the
  order of 0.007% of a CPU core's time budget. Not a meaningful cost
  next to WebGL rendering.

## Drop Detection (`checkDrop`)

Added after real-world testing showed neither beat mode reacts any
differently to a drop than to a regular beat - which makes sense, since
both are built to find/pace to *periodic* beats, and a drop isn't
"a beat," it's a rare, dramatic, **sustained** jump in overall
loudness (often preceded by a quieter buildup).

Runs unconditionally whenever Beat-Driven Cycle is on, regardless of
which beat mode is selected - it's a layered addition, not a third
competing mode:

1. Track broadband energy (average across *all* FFT bins, not just
   the bass ones the beat detectors use - a drop typically brings in
   sub-bass and other frequencies together, not just a kick).
2. Maintain a 15s rolling baseline, computed excluding the most recent
   300ms (`DROP_SUSTAIN_MS`) - so a developing drop doesn't drag its
   own baseline up while it's still forming.
3. Compare the average of that most recent 300ms against the
   baseline. It only counts as a drop if that recent window's *average*
   clears a large threshold (1.8x, `DROP_THRESHOLD` - deliberately much
   higher than a per-beat onset threshold, since a drop should be
   unmistakable) - not just an instantaneous spike, which is what
   actually distinguishes "a drop" from "one loud transient."
4. A 5s cooldown (`DROP_COOLDOWN_MS`) between detections - drops are
   rare, and a track's energy staying elevated after one shouldn't
   keep re-triggering.

Verified with a synthetic track (offline harness, not the live app):
10s of moderate groove energy, a 4s rising buildup, a brief near-silent
gap, then 6s of sustained high energy simulating a drop. Result: **one
detection, ~200ms after the drop hits, none during the groove or the
buildup** despite the buildup itself getting progressively louder -
the sustained-average requirement correctly didn't mistake a gradual
rise for the drop's sudden, sustained jump.

## References

Not literal implementations of these, but the general approach (onset
detection via spectral flux; periodicity/tempo estimation via
autocorrelation of an onset-strength signal) is standard MIR practice
described in:

- Bello, J.P., Daudet, L., Abdallah, S., Duxbury, C., Davies, M., &
  Sandler, M.B. (2005). "A Tutorial on Onset Detection in Music
  Signals." *IEEE Transactions on Speech and Audio Processing*,
  13(5), 1035-1047. - the standard reference for onset detection
  methods including spectral flux.
- Dixon, S. (2001). "Automatic extraction of tempo and beat from
  expressive performances." *Journal of New Music Research*, 30(1),
  39-58. - autocorrelation of an onset-detection function for tempo
  induction, closest in spirit to `estimateTempoPeriodMs()`.
- Scheirer, E.D. (1998). "Tempo and beat analysis of acoustic musical
  signals." *Journal of the Acoustical Society of America*, 103(1),
  588-601. - the classic (more elaborate) comb-filter-bank resonator
  approach to the same problem; not what's implemented here, but the
  usual starting point for anyone going further with this.

Real DJ software (Serato, rekordbox, Traktor) mostly sidesteps the
hard part by analyzing the whole track offline first to build a fixed
beat grid - a genuinely easier problem than reacting live to an
opaque, already-mixed system-audio stream with no lookahead, which is
what this app is doing.
