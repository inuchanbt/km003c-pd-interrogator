# USB PD decoding

English · [日本語](PDO-DECODING.ja.md)

The Python and phone decoders preserve the raw data. The importer shows the main ratings first and lets you open the decoded flags and raw bytes for each field. Saved history keeps QR pages and decodes them when reopened.

| Data | Specification and decoding |
| --- | --- |
| Source Status | PD 3.2 Table 6.56. Offsets 0–6 hold internal temperature, input, battery bitmap, events, temperature status, power limits, and power state/LED. Six-byte legacy payloads are supported. Bytes beyond offset 6 stay raw because KPD1 omits the extended header. |
| Source Identity | Tables 6.30, 6.34, 6.38–41. Verify the structured Discover Identity ACK and distinguish the SOP layout from cable identity. Decode ID Header, XID, Product VDO, and known UFP 1.3 and DFP 1.2 layouts. A DRD response requires a zero pad between the two role VDOs. |
| Source Capabilities Extended | Table 6.55. Show VID, PID, XID, FW and HW versions, and SPR/EPR PDP from offsets 23 and 24. A short legacy reply may omit EPR PDP. |
| Source Info | Table 6.52. Preserve all three one-watt fields in SIDO. Bit 31 identifies port type and bits 30–24 remain reserved. |
| Fixed PDO | Table 6.9. Decode 50 mV and 10 mA units and keep the peak-current code. Apply capability flags only to the first fixed 5 V PDO. Show the unchunked and EPR flags according to the reported revision, or keep their bit values visible when it is unknown. |
| Battery and Variable PDO | Tables 6.11–12. Both voltage bounds use 50 mV units. The low ten bits use 250 mW for Battery and 10 mA for Variable. |
| SPR and EPR AVS | Tables 6.14–16. SPR AVS preserves the 9–15 V and 15–20 V current bands in 10 mA units. EPR AVS has voltage bounds in 100 mV units and PDP in watts. The current shown at maximum voltage is explicitly a PDP equivalent. Preserve reserved slots and PDO order. |
| Cable Identity | Tables 6.42–43 and the PD 2.0 layouts. Decode ratings, speed, cable VDO version code, connector, latency, and VCONN termination. Active VDO 1 version 1.3 also shows SBU and SOP″ controller fields. Standalone four-word captures identify the PD 3.x interpretation as an assumption. |

Zero temperature/status codes can mean unsupported, while an unset event flag means no corresponding event was reported. The PPS operating-mode bit is meaningful only during PPS. Unknown VDO layouts and reserved values remain available as raw data. An XID or a successful reply does not establish certification.

Measured regression examples include Source Status `0006000000000000` (external AC and temperature unsupported), Identity `FF00A841 01C032AC 00000000 00220000 40000000` (Power Brick, VID 32AC, PID 0022), SIDO `01F0F080` (reported 1 W, present and maximum 240 W), and cable VDOs `1C6020C2 00000000 0A140310 460A2643` (50 V, 5 A, EPR, USB4 Gen 3). Synthetic tests cover Battery, Variable, active cables, malformed lengths, and reserved values.

[USB-IF specifications](https://www.usb.org/document-library/usb-power-delivery) · [USB PD 3.2 v1.0 tables](https://studylib.net/doc/27586979/usb-pd-r3.2-v1.0-2023-10) · [Chromium EC VDO definitions](https://chromium.googlesource.com/chromiumos/platform/ec/+/master/include/usb_pd_vdo.h)
