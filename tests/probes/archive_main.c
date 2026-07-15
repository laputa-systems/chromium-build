#include <stdio.h>

int archive_answer(void);

int main(void) {
    int value = archive_answer() + 1;
    printf("archive-ok-%d\n", value);
    return value == 42 ? 0 : 1;
}
