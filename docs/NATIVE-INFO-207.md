# Additional PD queries on the meter

English · [日本語](NATIVE-INFO-207.ja.md)

The exact-build HW 2.3 / stock 2.0.7 overlay uses the stock policy engine. Disconnect downstream equipment and run stock PD detection. Once the engine is stable, the overlay runs its applicable queries. Hold Left and Right to open the QR display. A pending query shows **READING PD**. The display changes to the completed QR result when ready. Exit closes the QR screen. Closing stock detection keeps the result and measurement ID in RAM until restart.

| Field | Acquisition |
| --- | --- |
| SPR/EPR PDOs | Copy the received PDOs owned by the current detection session |
| Revision | Get_Revision and its RMDO |
| Source identification | Get_Source_Cap_Extended |
| Source PDP | Get_Source_Info |
| Source status | Get_Status |
| Manufacturer | Get_Manufacturer_Info with target 0 and reference 0 |
| Source identity | SOP Discover Identity, including NAK and BUSY |
| PPS status | Get_PPS_Status only after a valid existing PPS contract |
| Cable identity | Observe the stock SOP′ Discover Identity request and response |

The query state machine waits for 250 ms of Ready after EPR and two seconds on SPR. If a source advertises EPR without entering it within four seconds, the overlay may continue on stable SPR. Each query checks transmission and the corresponding reply. Send failure, timeout, invalid response, or a reset during the sequence can leave later fields unrequested. A completed capture can contain unsuccessful fields.

The overlay preserves the stock negotiation and uses no independent voltage selection, role swap, or EPR entry. Avoid PC PD commands while collection is running. If e-marker emulation was enabled during stock detection, the cable result carries that setting. The setting does not identify the physical sender of an individual VDO.

## Measure a manual PPS contract

1. With side HID power and both measurement ports empty, open **Charging protocol**. Choose the current PD setting beside **Be ready!** and set it to **PD3.0 (SPR)** for an EPR-capable source.
2. Connect the source and leave the output empty. In **Power Delivery**, select PPS and adjust its voltage. Wait until measured output follows the setpoint.
3. Hold Left and Right on the adjustment screen. Scan both PPS QR pages. The results distinguish the outgoing Request RDO from the source's Status reply. A no-load reply may report 0 A even when the request specifies a higher operating current.
4. If the meter shows **NO PPS**, establish the SPR PPS contract again.

The stock cable check is a separate mode. See [capture ownership and cable QR export](NATIVE-CAPTURE-207.md) and [hardware verification](HARDWARE-2026-11-05.md).
