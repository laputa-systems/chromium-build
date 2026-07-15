#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>

static _Thread_local int tls_value = 7;
static atomic_int counter;

static void *worker(void *arg) {
    (void)arg;
    tls_value += 5;
    atomic_fetch_add_explicit(&counter, 1, memory_order_relaxed);
    return NULL;
}

int main(void) {
    pthread_t thread;
    atomic_init(&counter, 0);
    if (pthread_create(&thread, NULL, worker, NULL) != 0) {
        return 1;
    }
    if (pthread_join(thread, NULL) != 0 || atomic_load(&counter) != 1) {
        return 2;
    }
    if (tls_value != 7) {
        return 3;
    }
    puts("c-runtime-ok");
    return 0;
}
