# Native QR screen on stock 2.0.7

English · [日本語](NATIVE-GUI-207.ja.md)

Hold Left and Right for about one second to open the current measurement. Left and Right cycle through its pages. Exit closes the QR screen and restores stock input. The same completed result can be reopened while it remains in RAM. Pending acquisition and no-data cases have separate screens.

The 10-pixel page label, such as `SAVED 01/06`, is centered below the QR code. The symbol has at least four blank modules on every side.

The overlay uses the stock LVGL image object, allocator, and input group. QR workspace and image data use stock heap allocations and are released on close. The GUI task continues to run the stock handler. Keeping the stock input group active outside the overlay lets the normal keys behave as before. The compact PPS image fits alongside the stock PPS adjustment screen.

Offline validation runs the ARM overlay against private stock ROM and RAM, checks button handling and allocation behavior, and decodes the rendered QR through jsQR. Hardware records are separate from the emulator results. See [overlay placement](OVERLAY-207.md), [capture behavior](NATIVE-CAPTURE-207.md), and [hardware verification](HARDWARE-2026-11-05.md).
