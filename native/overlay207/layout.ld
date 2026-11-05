/* SPDX-License-Identifier: MIT. Offline integration probe; not an installer. */
ENTRY(kpd207_task_gate)
MEMORY {
    GATE (rx) : ORIGIN = 0x738c0, LENGTH = 0x740
    TAIL (rx) : ORIGIN = 0x74000, LENGTH = 0x8000
}
SECTIONS {
    .gate : ALIGN(4) {
        KEEP(*gate_entry.o(.text*))
        *gate.o(.text* .rodata*)
    } > GATE
    .header : ALIGN(4) { KEEP(*(.kpd207.header)) } > TAIL
    .text : ALIGN(4) { *(.text*) *(.rodata*) } > TAIL
    .ARM.exidx : ALIGN(4) { *(.ARM.exidx*) } > TAIL
    .data : { *(.data*) } > TAIL
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } > TAIL
    /DISCARD/ : { *(.comment) *(.ARM.attributes) }
    ASSERT(SIZEOF(.gate) <= LENGTH(GATE), "Gate exceeds stock gap")
    ASSERT(SIZEOF(.header) == 64, "Extension header must be 64 bytes")
    ASSERT(SIZEOF(.data) == 0, "Overlay must have no initialized mutable globals")
    ASSERT(SIZEOF(.bss) == 0, "No unreserved stock SRAM may be used")
}
