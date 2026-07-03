class PCMPlayer extends AudioWorkletProcessor {

    constructor() {

        super();

        this.samples = [];

        this.port.onmessage = e => {

            const data =
                new Float32Array(e.data);

            this.samples.push(...data);

        };
    }


    process(inputs, outputs) {

        if (this.samples.length > 0) {
            console.log(
                "WORKLET PLAYING",
                this.samples.length
            );
        }

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
