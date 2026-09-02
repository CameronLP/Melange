// main.js
//
// Copyright 2026 Cameron
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// SPDX-License-Identifier: GPL-3.0-or-later

import butterchurn from "butterchurn";
import presets from "butterchurn-presets";
import { getPresets as getBaronPresets } from "butterchurn-presets-baron";
// Default-import interop breaks for this package: its CJS bundle sets
// __esModule: true without ever setting a .default export (only the
// named convertPreset), so `import x from "..."` resolves to
// undefined under Vite/Rollup's interop. A named import sidesteps it.
import { convertPreset as convertMilkdropPreset } from "milkdrop-preset-converter";

// milkdrop-preset-converter has a bug affecting most real-world .milk
// presets: it mistranslates a chained addition in the original
// MilkDrop warp/comp shader code (roughly `A + B + C`) into invalid
// GLSL - `bvecN(A) && bvecN(B)` - since GLSL's &&/|| only accept a
// scalar bool, never a vector-of-bool. That's not just cosmetically
// wrong: feeding it to WebGL has been observed to hang the renderer
// instead of failing with a fast compile error. This walks the
// generated shader source and rewrites the mistranslated pattern back
// to the addition it almost certainly started as. Verified against a
// large sample of real presets: zero residual bad matches afterward.
function findMatchingParen(str, openIdx) {

    let depth = 0;

    for (let j = openIdx; j < str.length; j++) {

        if (str[j] === "(") depth++;
        else if (str[j] === ")") {
            depth--;
            if (depth === 0) return j;
        }
    }

    return -1;
}

function matchBvecCastAt(str, i) {

    const m = /^bvec[234]\s*\(/.exec(str.slice(i, i + 20));

    if (!m) return null;

    return { openParenIdx: i + m[0].length - 1 };
}

// The right-hand side is usually another bvecN(...) cast, but in a
// 3+-way chained expression it's a bare parenthesized group wrapping
// a further bvecN(...) && bvecN(...) - handled on a later pass once
// this one has unwrapped the outer layer.
function matchRhsAt(str, i) {

    const bvecCast = matchBvecCastAt(str, i);

    if (bvecCast) return bvecCast;

    return str[i] === "(" ? { openParenIdx: i } : null;
}

function repairBadShaderPass(str) {

    let out = "";
    let i = 0;
    let changed = false;

    while (i < str.length) {

        const bvecCast = matchBvecCastAt(str, i);

        if (bvecCast) {

            const closeA = findMatchingParen(str, bvecCast.openParenIdx);

            if (closeA !== -1) {

                const afterA = closeA + 1;

                const andOrMatch =
                    /^\s*(&&|\|\|)\s*/.exec(str.slice(afterA, afterA + 10));

                if (andOrMatch) {

                    const rhsStart = afterA + andOrMatch[0].length;
                    const rhs = matchRhsAt(str, rhsStart);

                    if (rhs) {

                        const closeB = findMatchingParen(str, rhs.openParenIdx);

                        if (closeB !== -1) {

                            const argA = str.slice(bvecCast.openParenIdx + 1, closeA);
                            const argB = str.slice(rhs.openParenIdx + 1, closeB);

                            out += `(${argA}) + (${argB})`;
                            i = closeB + 1;
                            changed = true;

                            continue;
                        }
                    }
                }
            }
        }

        out += str[i];
        i++;
    }

    return { out, changed };
}

function repairBadShader(str) {

    for (let pass = 0; pass < 10; pass++) {

        const { out, changed } = repairBadShaderPass(str);

        str = out;

        if (!changed) break;
    }

    return str;
}


let audioSource = null;
let currentStream = null;
let pcmNode = null;
let analyser = null;
let pcmReady = false;
let currentAudioNode = null;
let butterchurnSource = null;

function debug(msg) {
    let d = document.getElementById("debug");

    if (!d) {
        d = document.createElement("pre");
        d.id = "debug";

        d.style.color = "lime";
        d.style.position = "absolute";
        d.style.top = "0";
        d.style.left = "0";
        d.style.zIndex = "9999";



        //d.style.width = "350px";
        d.style.width = "100%";
        d.style.height = "100vh";
        d.style.overflowY = "auto";
        d.style.boxSizing = "border-box";

        d.style.background =
            "rgba(0,0,0,0.7)";

        d.style.margin = "0";
        //d.style.padding = "10px";
        d.style.padding = "50px 10px 10px 10px";


        // hidden on startup
        d.style.display = "none";

        document.body.appendChild(d);
    }

    d.textContent += msg + "\n";
    d.scrollTop = d.scrollHeight;

    try {
        window.webkit.messageHandlers.debug.postMessage(String(msg));
    } catch(e) {}
}


window.onerror = function(
    message,
    source,
    lineno,
    colno,
    error
) {
    debug(
        "ERROR: " +
        message +
        " line=" +
        lineno +
        " col=" +
        colno
    );

    console.error(
        message,
        source,
        lineno,
        colno,
        error
    );
};


// Forwards console.error/warn (e.g. WebGL shader compile failures
// logged from inside butterchurn's own renderer) to the same debug
// channel - those don't throw, so window.onerror above never sees
// them, and without this they'd otherwise only be visible in a web
// inspector this embedded view doesn't expose.
const nativeConsoleError = console.error.bind(console);
const nativeConsoleWarn = console.warn.bind(console);

console.error = function(...args) {
    debug("CONSOLE ERROR: " + args.map(String).join(" "));
    nativeConsoleError(...args);
};

console.warn = function(...args) {
    debug("CONSOLE WARN: " + args.map(String).join(" "));
    nativeConsoleWarn(...args);
};


debug("JS STARTED");




const canvas =
    document.getElementById("canvas");


// The canvas element itself always fills the window via CSS (100%
// width/height, index.html) - this only changes its *internal* pixel
// buffer resolution, which the browser then scales to fit. Below 1.0
// trades sharpness for less GPU work; above 1.0 supersamples.
let renderScale = 1.0;

canvas.width =
    Math.round(window.innerWidth * renderScale);

canvas.height =
    Math.round(window.innerHeight * renderScale);


const audioContext =
    new AudioContext();


debug(
    "AudioContext: " +
    audioContext.state
);





document.addEventListener(
    "click",
    () => {
        audioContext.resume();
    },
    { once:true }
);



// Debug Controls
// Toggle debug panel with F12
document.addEventListener(
    "keydown",
    (event) => {

        if (event.key === "F12") {

            const d =
                document.getElementById("debug");

            if (d) {
                d.style.display =
                    d.style.display === "none"
                    ? "block"
                    : "none";
            }
        }

        if (event.key === "Delete") {
          document.getElementById("debug").textContent = "";
        }


    }
);



const visualizer =
    butterchurn.createVisualizer(
        audioContext,
        canvas,
        {
            width: canvas.width,
            height: canvas.height
        }
    );


// Sensitivity control: sits between whichever source is currently
// selected and butterchurn, so it works the same for system audio and
// mic. Stays permanently connected to butterchurn - switching sources
// only ever reconnects the source side (see connectButterchurn),
// never this node, so the gain setting persists across source changes.
const sensitivityGain =
    audioContext.createGain();

sensitivityGain.gain.value = 1.0;

visualizer.connectAudio(sensitivityGain);


window.setSensitivity = function(value) {

    sensitivityGain.gain.value = value;

    debug("Sensitivity: " + value);
};


// Beat-driven auto-cycle, tapped off sensitivityGain rather than the
// active source directly, since that's the one node that stays
// connected regardless of whether the current source is system audio
// or mic (see connectButterchurn). Two selectable modes - see
// docs/beat-detection.md for the full writeup of both and why the
// second one exists.
const beatAnalyser =
    audioContext.createAnalyser();

beatAnalyser.fftSize = 256;
beatAnalyser.smoothingTimeConstant = 0.3;

sensitivityGain.connect(beatAnalyser);

const beatFreqData =
    new Uint8Array(beatAnalyser.frequencyBinCount);

let beatCycleEnabled = false;
let beatMode = "energy"; // "energy" | "tempo"
let lastBeatAdvanceTime = 0;

let beatMinIntervalMs = 2000;  // cooldown so one hit's decay doesn't
                                // trigger several advances in a row
let beatEnergyThreshold = 1.4; // vs. the comparison average (rolling
                                // long-term in energy mode, short
                                // local window in tempo mode)
let beatSilenceFloor = 40;     // ignore near-silence entirely


window.setBeatCycle = function(enabled) {
    beatCycleEnabled = enabled;
};

window.setBeatSensitivity = function(threshold) {
    beatEnergyThreshold = threshold;
};

window.setBeatCooldown = function(seconds) {
    beatMinIntervalMs = seconds * 1000;
};

window.setBeatSilenceFloor = function(value) {
    beatSilenceFloor = value;
};

window.setBeatMode = function(mode) {

    beatMode = mode;

    // Stale history from the other mode isn't meaningful here -
    // start clean rather than let it bias the first few detections.
    bassEnergyHistory = [];
    fluxHistory = [];
    previousMagnitudes = null;
    estimatedPeriodMs = null;
    lastOnsetTime = -Infinity;
};


function checkBeat(now) {

    if (!beatCycleEnabled) return;

    if (beatMode === "tempo") {
        checkBeatTempo(now);
    } else {
        checkBeatEnergy(now);
    }

    // Runs alongside whichever mode is selected above, not as a third
    // competing mode - a drop is a fundamentally different event from
    // "a beat" (rare, dramatic, sustained), so neither Energy
    // Threshold nor Tempo Tracking has any particular affinity for
    // it; they'd just treat a drop's kick like any other beat.
    checkDrop(now);
}


// --- Mode 1: energy threshold -----------------------------------
//
// Bass energy vs. its own rolling ~1s average. Simple and reliable
// for a normal playlist (songs starting from silence, quieter verses
// vs. louder choruses), but needs loud/quiet contrast to find
// anything - a relentless, evenly-loud four-on-the-floor mix has
// little such contrast, so the average just rises to match the
// constant kick and nothing looks like an "onset" relative to it.

let bassEnergyHistory = [];

const BEAT_ENERGY_HISTORY_SIZE = 43; // ~1s of rolling average - not
                                      // exposed as a setting, just the
                                      // averaging window length

function checkBeatEnergy(now) {

    beatAnalyser.getByteFrequencyData(beatFreqData);

    // Roughly the first ~1.4kHz of a 256-point FFT at 44.1kHz.
    const bassBins = 8;
    let bassSum = 0;

    for (let i = 0; i < bassBins; i++) {
        bassSum += beatFreqData[i];
    }

    const bassEnergy = bassSum / bassBins;

    bassEnergyHistory.push(bassEnergy);

    if (bassEnergyHistory.length > BEAT_ENERGY_HISTORY_SIZE) {
        bassEnergyHistory.shift();
    }

    const avgEnergy =
        bassEnergyHistory.reduce((a, b) => a + b, 0) /
        bassEnergyHistory.length;

    const isOnset =
        bassEnergy > avgEnergy * beatEnergyThreshold &&
        bassEnergy > beatSilenceFloor;

    reportAndMaybeTrigger(now, isOnset, beatMinIntervalMs);
}


// --- Mode 2: tempo tracking --------------------------------------
//
// A lightweight, real-time simplification of the standard MIR
// approach (spectral flux onset detection + autocorrelation-based
// periodicity estimation - see docs/beat-detection.md for
// references). Spectral flux measures the *rise* in each frequency
// bin frame-to-frame rather than the absolute level, so a series of
// equally-loud kicks still produces a distinct spike at each attack
// even though the overall energy level never drops in between -
// exactly the case an energy-vs-long-average test misses.
// Autocorrelating that flux signal finds its dominant repeating
// period (the tempo), which paces triggering: an onset only counts
// once it's plausible for it to be the *next* beat, not just any
// local peak (a hi-hat, a vocal transient, ...).

let previousMagnitudes = null;
let fluxHistory = []; // { time, flux }, most recent last

const FLUX_HISTORY_SECONDS = 8;
const FLUX_BINS = 32; // roughly the low end where rhythmic/
                       // percussive content concentrates

let estimatedPeriodMs = null;
let lastTempoEstimateTime = 0;

const TEMPO_ESTIMATE_INTERVAL_MS = 1000; // re-autocorrelate at most
                                          // once a second - the whole
                                          // point is a *stable*
                                          // period estimate, and
                                          // it's also the relatively
                                          // expensive part (see
                                          // docs/beat-detection.md
                                          // for actual op counts)
const TEMPO_MIN_BPM = 60;
const TEMPO_MAX_BPM = 180;
const TEMPO_RESAMPLE_STEP_MS = 20; // 50Hz grid for autocorrelation -
                                    // fine enough that the true
                                    // period isn't lost to grid
                                    // quantization (measured: a 50ms
                                    // grid was coarse enough to
                                    // reliably mis-lock onto tempo
                                    // octaves - see below)
const LOCAL_PEAK_WINDOW_MS = 300;


function computeSpectralFlux() {

    beatAnalyser.getByteFrequencyData(beatFreqData);

    if (!previousMagnitudes) {
        previousMagnitudes = new Uint8Array(beatFreqData.length);
    }

    let flux = 0;

    for (let i = 0; i < FLUX_BINS; i++) {

        const diff = beatFreqData[i] - previousMagnitudes[i];

        if (diff > 0) flux += diff;
    }

    previousMagnitudes.set(beatFreqData);

    return flux;
}


// Onset-strength autocorrelation, resampled onto a uniform grid
// first since rAF-driven samples aren't perfectly evenly spaced.
function estimateTempoPeriodMs(history, now) {

    const windowMs = FLUX_HISTORY_SECONDS * 1000;
    const numSamples = Math.floor(windowMs / TEMPO_RESAMPLE_STEP_MS);
    const startTime = now - windowMs;

    const series = new Float32Array(numSamples);
    let hIdx = 0;

    for (let i = 0; i < numSamples; i++) {

        const t = startTime + i * TEMPO_RESAMPLE_STEP_MS;

        while (
            hIdx < history.length - 1 &&
            history[hIdx + 1].time < t
        ) {
            hIdx++;
        }

        series[i] = history.length ? history[hIdx].flux : 0;
    }

    const minLag = Math.round(
        (60000 / TEMPO_MAX_BPM) / TEMPO_RESAMPLE_STEP_MS
    );

    const maxLag = Math.round(
        (60000 / TEMPO_MIN_BPM) / TEMPO_RESAMPLE_STEP_MS
    );

    const scores = new Float32Array(maxLag + 1);

    for (let lag = minLag; lag <= maxLag; lag++) {

        let sum = 0;

        for (let i = 0; i + lag < numSamples; i++) {
            sum += series[i] * series[i + lag];
        }

        scores[lag] = sum;
    }

    let bestLag = -1;
    let bestScore = 0;

    for (let lag = minLag; lag <= maxLag; lag++) {

        if (scores[lag] > bestScore) {
            bestScore = scores[lag];
            bestLag = lag;
        }
    }

    // Octave-error correction: raw autocorrelation is well known to
    // bias toward period-doublings (confirmed empirically here - an
    // early version of this without the correction reliably locked
    // onto half the true tempo). If half the best lag is still in
    // range and scores reasonably close to the best, it's the more
    // likely true (faster) fundamental.
    if (bestLag > 0) {

        const halfLag = Math.round(bestLag / 2);

        if (halfLag >= minLag && scores[halfLag] > bestScore * 0.6) {
            bestLag = halfLag;
        }
    }

    return bestLag > 0 ? bestLag * TEMPO_RESAMPLE_STEP_MS : null;
}


let lastOnsetTime = -Infinity;

const RECENT_PEAK_WINDOW = 5;    // frames - causal local-max check
const ONSET_REFRACTORY_MS = 100; // no two onsets closer than this,
                                  // even at 180 BPM beats are ~333ms
                                  // apart


// Measured against a synthetic 128 BPM test signal: the first version
// of this compared flux to a local average that *included the sample
// being tested* (computed after pushing it into fluxHistory), and had
// no local-max or refractory check - so it fired on almost every
// frame regardless of whether it was actually a rising transient
// (confirmed from real logs: ~1375 onset detections for 58 actual
// advances, paced only by the cooldown, not real beat detection).
// Excluding the current sample from its own comparison average, plus
// requiring it be a genuine local maximum (not just "above average"),
// cut false positives by roughly half to two-thirds across a range of
// synthetic noise levels in testing.
function checkBeatTempo(now) {

    const flux = computeSpectralFlux();

    // Local peak-picking against fluxHistory as it stood *before*
    // this sample - it's the rise relative to recent context that
    // matters, not the absolute level, and comparing against itself
    // made the threshold nearly meaningless.
    let localSum = 0;
    let localCount = 0;
    let recentMax = 0;

    for (let i = fluxHistory.length - 1; i >= 0; i--) {

        if (now - fluxHistory[i].time > LOCAL_PEAK_WINDOW_MS) break;

        localSum += fluxHistory[i].flux;
        localCount++;

        if (
            localCount <= RECENT_PEAK_WINDOW &&
            fluxHistory[i].flux > recentMax
        ) {
            recentMax = fluxHistory[i].flux;
        }
    }

    const localAvg = localCount ? localSum / localCount : 0;

    const isOnset =
        flux >= recentMax &&
        flux > localAvg * beatEnergyThreshold &&
        flux > beatSilenceFloor &&
        now - lastOnsetTime > ONSET_REFRACTORY_MS;

    if (isOnset) {
        lastOnsetTime = now;
    }

    fluxHistory.push({ time: now, flux });

    const cutoff = now - FLUX_HISTORY_SECONDS * 1000;

    while (fluxHistory.length && fluxHistory[0].time < cutoff) {
        fluxHistory.shift();
    }

    if (
        fluxHistory.length > 40 &&
        now - lastTempoEstimateTime > TEMPO_ESTIMATE_INTERVAL_MS
    ) {
        lastTempoEstimateTime = now;
        estimatedPeriodMs = estimateTempoPeriodMs(fluxHistory, now);
    }

    // Once a tempo estimate exists, don't let a stray onset (a
    // hi-hat, a vocal transient) advance faster than roughly every
    // half beat - otherwise fall back to the plain cooldown.
    const minGap = estimatedPeriodMs
        ? Math.max(beatMinIntervalMs, estimatedPeriodMs * 0.5)
        : beatMinIntervalMs;

    reportAndMaybeTrigger(now, isOnset, minGap);
}


// Shared by both modes: logs every qualifying onset (cooldown or
// not, so the detector's actual behavior is visible - see
// on_webview_debug_message in window.py for the BEAT_NAV_NEXT toast),
// and fires the advance once the given cooldown/gap has elapsed.
function reportAndMaybeTrigger(now, isOnset, minGap) {

    if (!isOnset) return;

    const cooledDown = now - lastBeatAdvanceTime > minGap;

    debug(
        "BEAT_DETECTED: mode=" + beatMode +
        (cooledDown ? " (advancing)" : " (cooling down)")
    );

    if (cooledDown) {
        lastBeatAdvanceTime = now;
        debug("BEAT_NAV_NEXT");
    }
}


// --- Drop detection ----------------------------------------------
//
// Separate from both beat modes above - a drop is a rare, dramatic,
// *sustained* jump in overall broadband energy (often after a
// quieter buildup), not just another beat, so neither mode has any
// particular affinity for it. Compares a short recent window against
// a long-term (15s) baseline that excludes that same recent window
// (so a developing drop doesn't drag its own baseline up while it's
// still forming), requiring the elevation to be both large and
// *sustained* across the whole recent window - not just one loud
// instant - to tell a real drop apart from a single big transient.

let overallEnergyHistory = []; // { time, energy }
let lastDropTime = -Infinity;

const DROP_HISTORY_SECONDS = 15;
const DROP_SUSTAIN_MS = 300;
const DROP_THRESHOLD = 1.8;   // vs. the long-term baseline - much
                               // bigger than a per-beat onset, since
                               // a drop should be unmistakable
const DROP_COOLDOWN_MS = 5000; // drops are rare; a track's energy
                                // staying elevated after one shouldn't
                                // keep re-triggering


function checkDrop(now) {

    beatAnalyser.getByteFrequencyData(beatFreqData);

    // Broadband, unlike the bass-focused beat detectors above - a
    // drop is characterized by a jump across most of the spectrum,
    // not just the low end.
    let sum = 0;

    for (let i = 0; i < beatFreqData.length; i++) {
        sum += beatFreqData[i];
    }

    const energy = sum / beatFreqData.length;

    overallEnergyHistory.push({ time: now, energy });

    const cutoff = now - DROP_HISTORY_SECONDS * 1000;

    while (
        overallEnergyHistory.length &&
        overallEnergyHistory[0].time < cutoff
    ) {
        overallEnergyHistory.shift();
    }

    let baseSum = 0, baseCount = 0;
    let recentSum = 0, recentCount = 0;

    for (const entry of overallEnergyHistory) {

        if (now - entry.time < DROP_SUSTAIN_MS) {
            recentSum += entry.energy;
            recentCount++;
        } else {
            baseSum += entry.energy;
            baseCount++;
        }
    }

    const baseline = baseCount ? baseSum / baseCount : 0;
    const recentAvg = recentCount ? recentSum / recentCount : 0;

    const isDrop =
        baseline > 0 &&
        recentAvg > baseline * DROP_THRESHOLD &&
        now - lastDropTime > DROP_COOLDOWN_MS;

    if (isDrop) {
        lastDropTime = now;
        debug("DROP_NAV_NEXT");
    }
}


debug("Visualizer created");







async function setupPCM() {

    debug("setupPCM START");

    try {

        await audioContext.resume();

        debug("AudioContext resumed");


        const workletCode = `
        class PCMPlayer extends AudioWorkletProcessor {

            constructor() {
                super();

                // Flat per-sample queues, not per-chunk: incoming PCM
                // chunks are almost always bigger than the 128-sample
                // render quantum, so shifting whole chunks off and
                // only reading their first 128 samples silently
                // dropped the rest of every chunk. Separate L/R queues
                // since the source audio is genuinely stereo (see
                // receiveAudio, which de-interleaves it before it gets
                // here) - collapsing both channels into one shared
                // queue would still alternate L/R samples as if they
                // were one continuous channel.
                this.samplesL = [];
                this.samplesR = [];

                // This queue never reaches audioContext.destination -
                // it only feeds the analyser for visualization, never
                // actual audio playback - so there's no reason to
                // preserve every sample in order. Without a cap, any
                // upstream burstiness or drift (GStreamer buffering,
                // the JS<->Python bridge, main-thread contention from
                // the render loop) just accumulates forever: the queue
                // grows, and the delay between real audio and what the
                // visualizer reacts to grows right along with it.
                // Capping it and dropping the oldest excess keeps
                // latency bounded at the cost of occasionally not
                // rendering every single sample, which is exactly the
                // right trade-off here.
                this.maxQueuedSamples = Math.round(sampleRate * 0.05);

                this.port.onmessage = e => {

                    const left = new Float32Array(e.data.left);
                    const right = new Float32Array(e.data.right);

                    for (let i = 0; i < left.length; i++) {
                        this.samplesL.push(left[i]);
                        this.samplesR.push(right[i]);
                    }

                    if (this.samplesL.length > this.maxQueuedSamples) {

                        const excess = this.samplesL.length - this.maxQueuedSamples;

                        this.samplesL.splice(0, excess);
                        this.samplesR.splice(0, excess);
                    }

                };
            }


            process(inputs, outputs) {

                const outL = outputs[0][0];
                const outR = outputs[0][1];

                for (let i = 0; i < outL.length; i++) {

                    outL[i] =
                        this.samplesL.length
                        ? this.samplesL.shift()
                        : 0;

                    outR[i] =
                        this.samplesR.length
                        ? this.samplesR.shift()
                        : 0;
                }

                return true;
            }
        }

        registerProcessor(
            "pcm-player",
            PCMPlayer
        );
        `;


            const blob =
                new Blob(
                    [workletCode],
                    { type: "application/javascript" }
                );


            const url =
                URL.createObjectURL(blob);


            await audioContext.audioWorklet.addModule(url);


            pcmNode =
                new AudioWorkletNode(
                    audioContext,
                    "pcm-player",
                    {
                        outputChannelCount: [2]
                    }
                );


            analyser = audioContext.createAnalyser();

            analyser.fftSize = 1024;
            analyser.smoothingTimeConstant = 0.8;


            pcmNode.connect(analyser);

            // no audio playback
            // analyser.connect(audioContext.destination);

            connectButterchurn(
                analyser,
                "SYSTEM PCM"
            );


            //visualizer.connectAudio(analyser);


            pcmReady = true;

            debug("PCM READY");

    } catch(e) {

        debug(
            "PCM SETUP ERROR: " +
            e.message
        );

        console.error(e);
    }
}

setupPCM();




const allPresets =
    presets.getPresets();


// Merged in on top of the base pack. Names collide across packs fairly
// often (both draw from the same community MilkDrop presets), so a
// colliding baron preset is suffixed rather than silently overwriting
// the base one.
const baronPresets =
    getBaronPresets();

for (const name of Object.keys(baronPresets)) {

    const key =
        name in allPresets
        ? name + " (baron)"
        : name;

    allPresets[key] = baronPresets[name];
}


const names =
    Object.keys(allPresets);


// Announces the current preset name to Python over the existing debug
// message channel (prefixed so on_webview_debug_message can tell it
// apart from ordinary log lines), which sets it as the window title -
// simpler and more native than an on-canvas overlay.
function announcePresetName(name) {
    debug("PRESET_NAME:" + name);
}


// The preset browser lives in native GTK (window.py), but the actual
// preset names only exist here (from butterchurn-presets, plus
// anything loaded via loadPresetFile) - so Python needs its own copy
// to build that list. Sent whenever the list changes rather than kept
// in sync incrementally, since it's just names/strings.
function announcePresetList() {
    debug("PRESET_LIST:" + JSON.stringify(names));
}


// Called from Python when a preset is picked in the native browser.
window.loadPresetByName = function(name) {

    if (!(name in allPresets)) {
        debug("loadPresetByName: unknown preset " + name);
        return;
    }

    goToPreset(names.indexOf(name), blendSeconds);
};


// currentPreset tracks what's showing now; presetHistory/historyPos
// give next/previous browser-style back/forward navigation - previous
// always retraces what was actually shown (regardless of shuffle),
// and next replays forward through that history before generating a
// new preset again once historyPos catches back up to the end.
let currentPreset = 0;
let presetHistory = [0];
let historyPos = 0;
let shuffleEnabled = false;
let blendSeconds = 3;


window.setBlendTime = function(seconds) {
    blendSeconds = seconds;
};


// User-curated "up next" list, distinct from shuffle/sequential
// advance - nextPreset() drains this first (see below). Python owns
// the visible queue dialog but never mutates presetQueue directly;
// it only calls these and reflects back whatever announceQueue()
// reports, same pattern as the preset browser and PRESET_LIST:.
let presetQueue = [];

function announceQueue() {
    debug("QUEUE:" + JSON.stringify(presetQueue));
}

window.enqueuePreset = function(name) {

    if (!(name in allPresets)) {
        debug("enqueuePreset: unknown preset " + name);
        return;
    }

    presetQueue.push(name);
    announceQueue();
};

window.removeQueueItem = function(index) {

    if (index < 0 || index >= presetQueue.length) return;

    presetQueue.splice(index, 1);
    announceQueue();
};

window.moveQueueItem = function(index, delta) {

    const newIndex = index + delta;

    if (
        index < 0 || index >= presetQueue.length ||
        newIndex < 0 || newIndex >= presetQueue.length
    ) {
        return;
    }

    const [item] = presetQueue.splice(index, 1);
    presetQueue.splice(newIndex, 0, item);

    announceQueue();
};


// Drag-and-drop reordering (arbitrary from -> to), unlike
// moveQueueItem's fixed ±1 step for the up/down buttons.
window.moveQueueItemTo = function(fromIndex, toIndex) {

    if (
        fromIndex < 0 || fromIndex >= presetQueue.length ||
        toIndex < 0 || toIndex >= presetQueue.length ||
        fromIndex === toIndex
    ) {
        return;
    }

    const [item] = presetQueue.splice(fromIndex, 1);
    presetQueue.splice(toIndex, 0, item);

    announceQueue();
};


// Used by next/loadPresetByName/loadPresetFile - anywhere a preset
// change should be recorded in history. Not used for plain back/
// forward movement within existing history (see previousPreset/the
// early-return in nextPreset), which just replays it instead.
function goToPreset(index, blendSeconds) {

    currentPreset = index;

    visualizer.loadPreset(
        allPresets[names[index]],
        blendSeconds
    );

    announcePresetName(names[index]);

    // A new selection after navigating back discards whatever forward
    // history there was, same as a browser tab after following a new
    // link mid-back-navigation.
    presetHistory.length = historyPos + 1;
    presetHistory.push(index);
    historyPos = presetHistory.length - 1;
}


visualizer.loadPreset(
    allPresets[names[0]],
    0
);

announcePresetName(names[0]);
announcePresetList();


debug("Preset loaded");


function resizeCanvas() {

    canvas.width =
        Math.round(window.innerWidth * renderScale);

    canvas.height =
        Math.round(window.innerHeight * renderScale);

    visualizer.setRendererSize(
        canvas.width,
        canvas.height
    );
}


resizeCanvas();


window.setRenderScale = function(scale) {
    renderScale = scale;
    resizeCanvas();
};


window.setAntiAliasing = function(enabled) {
    visualizer.setOutputAA(enabled);
};


// 0 means uncapped - render on every animation frame, same as before
// this existed.
let targetFps = 0;
let lastRenderTime = 0;

window.setFramerate = function(fps) {
    targetFps = fps;
};

window.setMeshSize = function(size) {

    // Matches Butterchurn's own default aspect ratio (48x36).
    visualizer.setInternalMeshSize(
        Math.round(size),
        Math.round(size * 0.75)
    );
};


function frame(now) {

    if (
        targetFps <= 0 ||
        now - lastRenderTime >= 1000 / targetFps
    ) {
        visualizer.render();
        lastRenderTime = now;
    }

    checkBeat(now);

    requestAnimationFrame(frame);
}


frame(0);


window.addEventListener(
    "resize",
    resizeCanvas
);




window.setShuffle = function(enabled) {
    shuffleEnabled = !!enabled;
};


let cycleTimer = null;
let cycleIntervalSeconds = 0;
let cycleJitter = 0; // 0-1 fraction of the interval, each direction


// A recursive setTimeout chain rather than setInterval, so jitter can
// pick a fresh randomized delay each cycle instead of one fixed
// period repeating forever.
function scheduleCycleTick() {

    const jitterFactor =
        1 + (Math.random() * 2 - 1) * cycleJitter;

    cycleTimer = setTimeout(
        () => {
            // No separate on/off toggle - routed through the same
            // "NAV_NEXT" debug message the on-canvas arrows use (see
            // the click handlers below) rather than calling
            // nextPreset() directly, so auto-cycling also respects
            // the preset lock (and its toast) via Python's existing
            // next_preset().
            debug("NAV_NEXT");
            scheduleCycleTick();
        },
        cycleIntervalSeconds * 1000 * jitterFactor
    );
}


// 0 (or below) means "off".
window.setCycleInterval = function(seconds) {

    if (cycleTimer) {
        clearTimeout(cycleTimer);
        cycleTimer = null;
    }

    cycleIntervalSeconds = seconds;

    if (seconds > 0) {
        scheduleCycleTick();
    }
};


window.setCycleJitter = function(percent) {
    cycleJitter = percent / 100;
};


window.nextPreset = function() {

    // A deliberately queued preset takes priority over both history
    // replay and shuffle/sequential advance - the user asked for it
    // specifically.
    if (presetQueue.length > 0) {

        const name = presetQueue.shift();

        announceQueue();

        if (name in allPresets) {
            goToPreset(names.indexOf(name), blendSeconds);
            return;
        }

        // Fell out of allPresets somehow - fall through to a normal
        // advance rather than getting stuck.
    }

    // Replay forward through history first (e.g. after previousPreset
    // moved back) rather than generating a new preset, so going back
    // then forward returns to what was actually showing.
    if (historyPos < presetHistory.length - 1) {

        historyPos++;
        currentPreset = presetHistory[historyPos];

        visualizer.loadPreset(
            allPresets[names[currentPreset]],
            blendSeconds
        );

        announcePresetName(names[currentPreset]);
        return;
    }

    let index;

    if (shuffleEnabled && names.length > 1) {

        do {
            index = Math.floor(Math.random() * names.length);
        } while (index === currentPreset);

    } else {
        index = (currentPreset + 1) % names.length;
    }

    goToPreset(index, blendSeconds);
};


window.previousPreset = function() {

    if (historyPos === 0) return;

    historyPos--;
    currentPreset = presetHistory[historyPos];

    visualizer.loadPreset(
        allPresets[names[currentPreset]],
        blendSeconds
    );

    announcePresetName(names[currentPreset]);
};


// Routed through Python (which calls back into nextPreset/
// previousPreset itself) rather than calling those directly, so the
// preset-lock check - and the native toast it shows - only has to
// live in one place, regardless of whether a change was requested
// from these arrows or the win.next-preset/win.previous-preset
// keyboard shortcuts.
document.getElementById("nav-prev").addEventListener(
    "click",
    () => debug("NAV_PREVIOUS")
);

document.getElementById("nav-next").addEventListener(
    "click",
    () => debug("NAV_NEXT")
);


// Called from Python (see load_preset_clicked in window.py) after the
// user picks a .milk or .json preset file via the native file chooser.
// base64Text is the raw file contents - base64 because preset text
// (MilkDrop especially) is full of quotes/backslashes/newlines that
// aren't safe to embed directly in a JS string literal the way
// evaluate_javascript() builds this call.
window.loadPresetFile = async function(base64Text, name) {

    try {

        const bytes = Uint8Array.from(
            atob(base64Text),
            c => c.charCodeAt(0)
        );

        const text = new TextDecoder("utf-8").decode(bytes);

        // Butterchurn presets are plain JSON; MilkDrop presets are a
        // custom key=value text format that isn't valid JSON. Sniffing
        // the content this way (rather than trusting the file
        // extension) means a renamed file still loads correctly.
        let preset;

        try {
            preset = JSON.parse(text);
        } catch(jsonError) {
            preset = await convertMilkdropPreset(text);
        }

        if (preset.warp) preset.warp = repairBadShader(preset.warp);
        if (preset.comp) preset.comp = repairBadShader(preset.comp);

        // Belt-and-suspenders: repairBadShader fixes every case seen
        // across a large real-world sample, but if some other variant
        // of the bug slips through, refusing to load is much safer
        // than risking the renderer hang invalid GLSL has caused here.
        const badShaderPattern = /bvec[234]\s*\([^;{}]*?\)\s*(&&|\|\|)/;

        if (
            (preset.warp && badShaderPattern.test(preset.warp)) ||
            (preset.comp && badShaderPattern.test(preset.comp))
        ) {
            throw new Error(
                "converted shader still has invalid GLSL (bvecN &&/||) " +
                "after repair - refusing to load to avoid hanging the renderer"
            );
        }

        allPresets[name] = preset;
        names.push(name);

        goToPreset(names.length - 1, 0);

        announcePresetList();

        debug("Loaded preset file: " + name);

    } catch(e) {

        // Prefixed (like PRESET_NAME:/PRESET_LIST:) so Python can
        // route it to a visible toast - a failed load otherwise has
        // no user-facing feedback at all, since this whole path only
        // ever reported to the debug log.
        debug("LOAD_PRESET_ERROR:" + e.message);

        console.error(e);
    }
};



window.setAudioSource = async function(type) {

    //debug(
    //    "RAW TYPE: [" + type + "] length=" + type.length
    //);

    type = type.replaceAll("'", "");

    debug("Audio source: " + type);

    if (audioContext.state !== "running") {
        await audioContext.resume();
        debug("AudioContext: " + audioContext.state);
    }


    if (type === "none") {
        resetButterchurnAudio();
        debug("Audio disabled");
        return;
    }


    if (type === "mic") {

        debug("MIC BLOCK ENTERED");

        if (!navigator.mediaDevices) {
            debug("NO MEDIA DEVICES API");
            return;
        }

        debug("mediaDevices exists");

        try {

            debug("Calling getUserMedia");

            let p = navigator.mediaDevices.getUserMedia({
                audio: true
            });

            debug("Promise created");

            currentStream = await p;

            debug("STREAM RECEIVED");

            debug("Tracks: " + currentStream.getAudioTracks().length);

            const track = currentStream.getAudioTracks()[0];

            debug("Track enabled: " + track.enabled);
            debug("Track muted: " + track.muted);
            debug("Track state: " + track.readyState);

            audioSource = audioContext.createMediaStreamSource(currentStream);

            debug("MediaStreamSource created");

            //visualizer.connectAudio(audioSource);
            //audioSource.connect(audioContext.destination);


            if (currentAudioNode) {
                try {
                    currentAudioNode.disconnect();
                } catch(e) {}
            }

            resetButterchurnAudio();

            connectButterchurn(
                audioSource,
                "MIC"
            );

            currentAudioNode = audioSource;

            debug("Butterchurn connected to MIC");




        } catch(e) {

            debug("GETUSERMEDIA ERROR: " + e.name + " " + e.message);
        }

        return;
    }



    if (type === "system") {

        debug("System PCM selected");


        if (!pcmReady) {
            debug("PCM not ready");
            return;
        }


        resetButterchurnAudio();


        connectButterchurn(
            analyser,
            "SYSTEM PCM"
        );


        currentAudioNode = analyser;


        debug("Butterchurn connected to SYSTEM PCM");
    }
};







// Python's GStreamer pipeline captures genuine interleaved stereo
// (channels=2 - see start_system_audio in window.py), i.e. the raw
// bytes here are L,R,L,R,... samples, not a sequence of samples from
// one channel. Treating them as one flat mono stream (as this used to
// do) fed alternating left/right values into the analyser as if they
// were consecutive samples of the same waveform - not just losing
// real stereo reactivity, but actively corrupting the frequency
// content Butterchurn's bass/mid/treb analysis runs on for every
// preset, not only stereo-aware ones.
window.receiveAudio = function(encoded) {

    const raw = atob(encoded);

    const int16 =
        new Int16Array(raw.length / 2);

    for (let i = 0; i < int16.length; i++) {
        int16[i] =
            raw.charCodeAt(i*2) |
            (raw.charCodeAt(i*2+1) << 8);

        // signed conversion
        if (int16[i] > 32767) {
            int16[i] -= 65536;
        }
    }


    // De-interleave s16 stereo -> two float32 channels.
    const frameCount = int16.length / 2;

    const left = new Float32Array(frameCount);
    const right = new Float32Array(frameCount);

    for (let i = 0; i < frameCount; i++) {
        left[i] = int16[i * 2] / 32768.0;
        right[i] = int16[i * 2 + 1] / 32768.0;
    }


    if (pcmNode) {
        pcmNode.port.postMessage({ left, right });
    }

};








function connectButterchurn(node, name) {

    if (butterchurnSource) {
        try {
            butterchurnSource.disconnect(sensitivityGain);
        } catch(e) {}
    }


    node.connect(sensitivityGain);

    butterchurnSource = node;

    debug(
        "Butterchurn source = " + name
    );
}

function resetButterchurnAudio() {

    if (butterchurnSource) {
        try {
            butterchurnSource.disconnect(sensitivityGain);
        } catch(e) {}
    }

    butterchurnSource = null;

    debug("Butterchurn audio reset");
}


// Signals Python that window.setAudioSource etc. are now defined and
// safe to call - the WebKit "load-changed" FINISHED event fires once
// the network fetch completes, which is before this module script has
// actually run, so calling setAudioSource() from Python in response to
// that would silently no-op with a ReferenceError.
debug("APP_READY");





