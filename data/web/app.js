const canvas =
    document.getElementById("canvas");

const ctx =
    canvas.getContext("2d");


function resize(){

    canvas.width =
        window.innerWidth;

    canvas.height =
        window.innerHeight;
}

window.onresize = resize;

resize();


let t = 0;


function draw(){

    t += 0.02;

    ctx.fillStyle =
        `rgb(
        ${128 + Math.sin(t)*128},
        ${128 + Math.sin(t+2)*128},
        ${128 + Math.sin(t+4)*128}
        )`;


    ctx.fillRect(
        0,
        0,
        canvas.width,
        canvas.height
    );


    requestAnimationFrame(draw);
}


draw();
