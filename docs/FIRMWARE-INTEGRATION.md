# Firmware integration

English · [日本語](FIRMWARE-INTEGRATION.ja.md)

The integration uses the known stock 2.0.7 application on HW 2.3. Stock USB PD and display code remain responsible for negotiation, interrupt handling, the LCD bus, and rendering. The overlay observes the stock capture, freezes the result, and presents it through the stock GUI.

The C library in [native/](../native/README.md) converts raw fields into KPD1 pages and QR rows. Freeze a snapshot while the capture owner can guarantee consistency, then release that lock before encoding pages. Invoke display operations on the stock GUI task. Keep PD and USB timing independent of the QR renderer.

Test in order using a host C build, ARM emulation, offline package and rollback verification, then exact-build hardware. Confirm boot, stock UI responsiveness, QR generation and reading, and a full restore before adding PD transactions. A stock image, bootloader image, calibration data, and diagnostic memory dumps stay in private work directories.

See [overlay placement and verification](OVERLAY-207.md), [stock GUI integration](NATIVE-GUI-207.md), and [hardware verification](HARDWARE-2026-11-05.md).
