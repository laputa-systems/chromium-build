.section .init
.globl _init
.type _init,@function
_init:
  endbr64
  subq $8, %rsp
.section .fini
.globl _fini
.type _fini,@function
_fini:
  endbr64
  subq $8, %rsp
