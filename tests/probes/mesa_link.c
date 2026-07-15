#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <gbm.h>
#include <xf86drm.h>

int main(void) {
    (void)eglGetError();
    (void)glGetString(GL_VENDOR);
    (void)gbm_create_device(-1);
    (void)drmGetVersion(-1);
    return 0;
}
