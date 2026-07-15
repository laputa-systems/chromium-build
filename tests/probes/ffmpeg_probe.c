#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <stdio.h>

int main(void) {
    unsigned codec = avcodec_version();
    unsigned format = avformat_version();
    unsigned util = avutil_version();
    if (codec == 0 || format == 0 || util == 0 || avformat_network_init() < 0) {
        return 1;
    }
    printf("ffmpeg-ok %u.%u.%u\n", codec >> 16, (codec >> 8) & 255, codec & 255);
    avformat_network_deinit();
    return 0;
}
