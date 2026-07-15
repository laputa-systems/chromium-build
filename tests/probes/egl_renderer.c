#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <stdio.h>

int main(void) {
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_platform_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    EGLDisplay display = EGL_NO_DISPLAY;
    if (get_platform_display != NULL) {
        display = get_platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    }
    if (display == EGL_NO_DISPLAY) {
        display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    }
    EGLint major = 0;
    EGLint minor = 0;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor)) {
        fprintf(stderr, "EGL initialization failed: 0x%x\n", eglGetError());
        return 1;
    }

    const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_NONE,
    };
    EGLConfig config;
    EGLint count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &count) || count != 1) {
        return 2;
    }
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    const EGLint surface_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT ||
        !eglMakeCurrent(display, surface, surface, context)) {
        return 3;
    }
    const GLubyte *vendor = glGetString(GL_VENDOR);
    if (vendor == NULL) {
        return 4;
    }
    printf("egl-renderer-ok %s %d.%d\n", vendor, major, minor);
    eglDestroyContext(display, context);
    eglDestroySurface(display, surface);
    eglTerminate(display);
    return 0;
}
