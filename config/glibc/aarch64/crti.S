.section .init,"ax",%progbits
.globl _init
.type _init,@function
_init:
  stp x29, x30, [sp, #-16]!
  ret
.section .fini,"ax",%progbits
.globl _fini
.type _fini,@function
_fini:
  stp x29, x30, [sp, #-16]!
  ret
