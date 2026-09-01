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


canvas.width =
    window.innerWidth;

canvas.height =
    window.innerHeight;


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

                // Flat per-sample queue, not per-chunk: incoming PCM
                // chunks are almost always bigger than the 128-sample
                // render quantum, so shifting whole chunks off and
                // only reading their first 128 samples silently
                // dropped the rest of every chunk.
                this.samples = [];

                this.port.onmessage = e => {

                    const data =
                        new Float32Array(e.data);

                    for (let i = 0; i < data.length; i++) {
                        this.samples.push(data[i]);
                    }

                };
            }


            process(inputs, outputs) {

                const out = outputs[0][0];

                for (let i = 0; i < out.length; i++) {

                    out[i] =
                        this.samples.length
                        ? this.samples.shift()
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
                    "pcm-player"
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

    currentPreset = names.indexOf(name);

    visualizer.loadPreset(allPresets[name], 3);

    announcePresetName(name);
};


visualizer.loadPreset(
    allPresets[names[0]],
    0
);

announcePresetName(names[0]);
announcePresetList();


debug("Preset loaded");


visualizer.setRendererSize(
    window.innerWidth,
    window.innerHeight
);


function frame() {

    visualizer.render();
    requestAnimationFrame(frame);
}


frame();


window.addEventListener(
    "resize",
    () => {

        canvas.width =
            window.innerWidth;

        canvas.height =
            window.innerHeight;


        visualizer.setRendererSize(
            canvas.width,
            canvas.height
        );

    }
);




let currentPreset = 0;




window.nextPreset = function() {

    currentPreset++;

    if (currentPreset >= names.length) {
        currentPreset = 0;
    }

    visualizer.loadPreset(
        allPresets[names[currentPreset]],
        5
    );

    announcePresetName(names[currentPreset]);
};


window.previousPreset = function() {

    currentPreset--;

    if (currentPreset < 0) {
        currentPreset = names.length - 1;
    }

    visualizer.loadPreset(
        allPresets[names[currentPreset]],
        5
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
        currentPreset = names.length - 1;

        visualizer.loadPreset(preset, 0);

        announcePresetName(name);
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


    // Convert s16 -> float32
    const floats =
        new Float32Array(int16.length);


    let max = 0;

    for (let i=0; i<int16.length; i++) {

        floats[i] =
            int16[i] / 32768.0;

        max = Math.max(
            max,
            Math.abs(floats[i])
        );
    }


    if (pcmNode) {
        pcmNode.port.postMessage(floats);
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





