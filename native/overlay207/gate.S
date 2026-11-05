/* SPDX-License-Identifier: MIT */
.syntax unified
.cpu cortex-m4
.thumb
.section .text.kpd207_task_gate,"ax",%progbits
.global kpd207_task_gate
.type kpd207_task_gate,%function
.thumb_func
kpd207_task_gate:
    /* Task-local R8 carries the selected callback through stock initialization.
     * The exact GUI task uses R4, but never writes R8. Callees follow AAPCS.
     * No fixed SRAM address or RTOS/LVGL structure offset is introduced. */
    push {r0, lr}
    bl kpd207_choose_callback
    mov r8, r0
    pop {r0, lr}
    /* Replay the eight stock bytes replaced by the entry branch + NOPs. */
    movw r4, #0x0224
    movt r4, #0x42a7
    ldr r12, =0x1ebb9
    bx r12
.size kpd207_task_gate, .-kpd207_task_gate
.ltorg
