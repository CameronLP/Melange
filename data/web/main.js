import butterchurn from "butterchurn";
import presets from "butterchurn-presets";


const canvas =
    document.getElementById("canvas");


const audioContext =
    new AudioContext();


const visualizer =
    butterchurn.createVisualizer(
        audioContext,
        canvas,
        {
            width: window.innerWidth,
            height: window.innerHeight
        }
    );


const allPresets =
    presets.getPresets();


visualizer.loadPreset(
    allPresets[
        Object.keys(allPresets)[0]
    ],
    0
);


// microphone input

async function startAudio(){

    const stream =
        await navigator.mediaDevices
        .getUserMedia({
            audio:true
        });


    const source =
        audioContext
        .createMediaStreamSource(
            stream
        );


    visualizer.connectAudio(
        source
    );
}


startAudio();



function resize(){

    visualizer.setRendererSize(
        window.innerWidth,
        window.innerHeight
    );
}


window.onresize = resize;


function render(){

    visualizer.render();

    requestAnimationFrame(
        render
    );
}


render();
