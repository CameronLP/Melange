import butterchurn from "butterchurn";
import presets from "butterchurn-presets";

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



// Visualizer key controls
document.addEventListener(
    "keydown",
    e => {

        if (e.key === " ") {
            nextPreset();
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

                this.samples = [];

                this.port.onmessage = e => {

                    const data =
                        new Float32Array(e.data);

                    this.samples.push(data);

                };
            }


            process(inputs, outputs) {

                const out = outputs[0][0];

                if (this.samples.length > 0) {

                    const block = this.samples.shift();

                    for (let i = 0; i < out.length; i++) {
                        out[i] = block[i] || 0;
                    }

                } else {

                    for (let i = 0; i < out.length; i++) {
                        out[i] = 0;
                    }
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


const names =
    Object.keys(allPresets);


visualizer.loadPreset(
    allPresets[names[0]],
    0
);


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





