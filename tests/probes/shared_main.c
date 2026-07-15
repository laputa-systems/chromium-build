#include <stdio.h>

int shared_answer(int value);

int main(void) {
    int value = shared_answer(41);
    printf("shared-ok-%d\n", value);
    return value == 42 ? 0 : 1;
}
