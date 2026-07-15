#include <nss.h>

int main(void) {
    if (NSS_NoDB_Init(NULL) != SECSuccess) {
        return 1;
    }
    return NSS_Shutdown() == SECSuccess ? 0 : 2;
}
