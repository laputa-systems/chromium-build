.text
.globl _start
.type _start,@function
_start:
  mov x29, #0
  mov x30, #0
  mov x5, x0
  ldr x1, [sp]
  add x2, sp, #8
  mov x6, sp
  adrp x0, main
  add x0, x0, :lo12:main
  mov x3, #0
  mov x4, #0
  bl __libc_start_main
  bl abort
.size _start, .-_start
.data
.globl __data_start
__data_start:
