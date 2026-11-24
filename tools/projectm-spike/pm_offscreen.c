// Offscreen projectM test: EGL surfaceless context + own FBO, no GTK.
// Build: gcc -DHAVE_FBO_API -I<prefix>/include pm_offscreen.c -o pm_offscreen -L<prefix>/lib64 -lprojectM-4 -lEGL -lGL -lm
// (drop -DHAVE_FBO_API for a 4.1.x build with the target-FBO patch)
// Usage: pm_offscreen <preset.milk|idle> <out.ppm> [frames]
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <projectM-4/projectM.h>

#define W 640
#define H 360

int main(int argc, char **argv)
{
    const char *preset = argc > 1 ? argv[1] : "idle";
    const char *out = argc > 2 ? argv[2] : "out.ppm";
    int frames = argc > 3 ? atoi(argv[3]) : 300;

    PFNEGLGETPLATFORMDISPLAYEXTPROC getPlatformDisplay =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay dpy = getPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (!eglInitialize(dpy, NULL, NULL)) { fprintf(stderr, "eglInitialize failed\n"); return 1; }
    eglBindAPI(EGL_OPENGL_API);
    EGLint ctxAttr[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 3,
                        EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
    EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, ctxAttr);
    if (ctx == EGL_NO_CONTEXT) { fprintf(stderr, "eglCreateContext failed\n"); return 1; }
    eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx);
    printf("GL %s | %s\n", glGetString(GL_VERSION), glGetString(GL_RENDERER));

    PFNGLGENFRAMEBUFFERSPROC genFb = (void *)eglGetProcAddress("glGenFramebuffers");
    PFNGLBINDFRAMEBUFFERPROC bindFb = (void *)eglGetProcAddress("glBindFramebuffer");
    PFNGLFRAMEBUFFERTEXTURE2DPROC fbTex = (void *)eglGetProcAddress("glFramebufferTexture2D");
    PFNGLCHECKFRAMEBUFFERSTATUSPROC fbStatus = (void *)eglGetProcAddress("glCheckFramebufferStatus");

    GLuint tex, fbo;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    genFb(1, &fbo);
    bindFb(GL_FRAMEBUFFER, fbo);
    fbTex(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    printf("FBO %u complete=%d\n", fbo, fbStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE);
    glClearColor(1, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);

    projectm_handle pm = projectm_create();
    printf("projectm_create -> %p\n", (void *)pm);
    projectm_set_window_size(pm, W, H);
    if (strcmp(preset, "idle") != 0)
        projectm_load_preset_file(pm, preset, false);

    float pcm[2 * 735];
    double phase = 0;
    for (int f = 0; f < frames; f++) {
        for (int i = 0; i < 735; i++) {  // 44.1kHz / 60fps, 110Hz tone with a 2Hz pulse
            float amp = 0.6f * (0.5f + 0.5f * (float)sin(f * 2 * M_PI * 2 / 60.0));
            float s = amp * (float)sin(phase);
            phase += 2 * M_PI * 110 / 44100.0;
            pcm[2 * i] = pcm[2 * i + 1] = s;
        }
        projectm_pcm_add_float(pm, pcm, 735, PROJECTM_STEREO);
#ifdef HAVE_FBO_API
        projectm_opengl_render_frame_fbo(pm, fbo);
#else
        bindFb(GL_FRAMEBUFFER, fbo);
        projectm_opengl_render_frame(pm);
#endif
    }
    glFinish();
    printf("glGetError=0x%x\n", glGetError());

    unsigned char *px = malloc(W * H * 4);
    bindFb(GL_READ_FRAMEBUFFER, fbo);
    glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, px);
    FILE *fp = fopen(out, "wb");
    fprintf(fp, "P6\n%d %d\n255\n", W, H);
    long sum = 0;
    for (int y = H - 1; y >= 0; y--)
        for (int x = 0; x < W; x++) {
            unsigned char *p = px + (y * W + x) * 4;
            fwrite(p, 1, 3, fp);
            sum += p[0] + p[1] + p[2];
        }
    fclose(fp);
    printf("mean rgb = %.2f / 255\n", sum / (double)(W * H * 3));
    projectm_destroy(pm);
    return 0;
}
