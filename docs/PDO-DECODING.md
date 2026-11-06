# USB PD decoding

English · [日本語](PDO-DECODING.ja.md)

The Python and phone decoders preserve the raw data. The importer shows the main ratings first and lets you open the decoded flags and raw bytes for each field. Saved history keeps QR pages and decodes them when reopened. The table numbers below refer to USB PD 3.2 v1.1, the version reported by the reference source.

| Data | Specification and decoding |
| --- | --- |
| Source Status | Table 6.55. Offsets 0–6 hold internal temperature, input, battery bitmap, events, temperature status, power limits, and power state/LED. Six-byte legacy captures omit the power state byte. Bytes beyond offset 6 stay raw because KPD1 lacks the Extended Header's Data Size. |
| Source Identity | Tables 6.29, 6.33, 6.37–40. Verify the structured Discover Identity ACK and distinguish the SOP layout from cable identity. Decode the ID Header, XID, Product VDO, and known UFP 1.3 and DFP 1.2 layouts. A DRD response requires a zero pad between the two role VDOs. |
| Source Capabilities Extended | Table 6.54. Show VID, PID, XID, FW and HW versions, and SPR/EPR PDP from offsets 23 and 24. A short legacy reply may omit EPR PDP. |
| Source Info | Table 6.51. Preserve all three one-watt fields in the single-word SIDO. Bit 31 identifies port type and bits 30–24 are reserved. |
| Fixed PDO | Table 6.9. Decode 50 mV and 10 mA units, with the peak-current code in bits 21–20. Apply capability flags only to the first fixed 5 V PDO. The display interprets B24 for PD 3.0 and later and B23 for PD 3.1 and later, based on the source's reported revision. With an unknown revision it displays the bit values. |
| Battery and Variable PDO | Tables 6.11–12. Type bits 31–30 are `01` for Battery and `10` for Variable. Both voltage bounds use 50 mV units. The low ten bits use 250 mW for Battery and 10 mA for Variable. |
| PPS APDO | Table 6.13. The voltage bounds use 100 mV units and the current uses 50 mA units. The raw word retains the Power Limited bit and the reserved bits. |
| SPR and EPR AVS | Tables 6.14–15. SPR AVS preserves the 9–15 V and 15–20 V current bands in 10 mA units. EPR AVS has voltage bounds in 100 mV units and PDP in watts. Both have peak-current codes in bits 27–26. The current shown at maximum EPR AVS voltage is a PDP equivalent at that voltage. |
| EPR PDO order | §6.5.15.1. The first seven slots hold the SPR PDOs and zero-filled unused slots. EPR PDOs begin at slot eight. The details retain the original slot numbers and zero values. |
| Cable Identity | Tables 6.41–42 and the PD 2.0 layouts. Decode ratings, speed, cable VDO version code, connector, latency, and VCONN termination. Active VDO 1 version 1.3 also shows SBU and SOP″ controller fields. Standalone four-word captures identify the PD 3.x interpretation as an assumption. |

Zero temperature and power-state codes indicate unsupported fields. Unset event and power-limit flags mean no corresponding flags were reported. The PPS operating-mode bit is meaningful during PPS. Unknown VDO layouts and reserved values remain available as raw data.

USB PD 3.2 v1.2 also defines a two-word Source Info layout with SIDO2. The current decoder supports the single-word layout and retains other lengths as raw data.

Measured regression examples include Source Status `0006000000000000` (external AC, temperature unsupported, eighth byte uninterpreted), Identity `FF00A841 01C032AC 00000000 00220000 40000000` (Power Brick, VID 32AC, PID 0022), SIDO `01F0F080` (reported 1 W, present and maximum 240 W), and cable VDOs `1C6020C2 00000000 0A140310 460A2643` (50 V, 5 A, EPR, USB4 Gen 3). Independent test vectors specify the expected PDO values for both decoders, including nonzero reserved bits to check bit masks. Further vectors cover active cables, short Status replies, and unsupported Identity layouts.

[USB-IF USB PD 3.2 v1.1 package](https://usb.org/sites/default/files/usb_pd_r3.2_v1.1.zip) · [USB-IF USB PD 3.2 v1.2 package](https://www.usb.org/sites/default/files/USB_PD_R3.2_V1.2%283%29_0.zip) · [Linux USB PD definitions](https://github.com/torvalds/linux/blob/master/include/dt-bindings/usb/pd.h) · [Chromium EC EPR slot handling](https://chromium.googlesource.com/chromiumos/platform/ec/+/da5a30aac716d8d9f1c5faf8f029ce75fccdb910/common/usb_pd_dual_role.c#87) · [Chromium EC VDO definitions](https://chromium.googlesource.com/chromiumos/platform/ec/+/master/include/usb_pd_vdo.h)
