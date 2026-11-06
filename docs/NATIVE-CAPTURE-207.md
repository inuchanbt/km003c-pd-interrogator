# Capture ownership and QR retention

English · [日本語](NATIVE-CAPTURE-207.ja.md)

The overlay copies SPR and EPR PDOs and additional PD replies belonging to the current stock detection. It excludes idle or stale cache entries. EPR PDOs can arrive after the SPR list, so the capture waits for the current session and preserves both lists independently. Each list keeps the received order and reserved slots.

The completed snapshot and its measurement ID remain in RAM across Exit and display reopen. A later detection can replace the snapshot. Restart clears it. Holding Left and Right opens the appropriate result, and a result without usable capture data displays **NO DATA**. Full state-machine inputs, memory locations, and replay procedures are in the [Japanese technical record](NATIVE-CAPTURE-207.ja.md).

## Standalone cable check

Run **USB-C eMarker** in the stock UI with the cable under test. Hold Left and Right on the result screen. Use Left and Right to move between the two QR pages. Exit returns to the stock result. The result preserves IdHead, CertStat, Product, and Cable words. If all four are zero but SVIDs are present, the overlay exports a partial result with the SVIDs. The phone importer keeps separate history entries for cable and power source measurements.

See [source queries and manual PPS](NATIVE-INFO-207.md), [KPD1](QR-FORMAT.md), and [hardware verification](HARDWARE-2026-11-05.md).
