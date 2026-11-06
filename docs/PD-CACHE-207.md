# Stock 2.0.7 PDO cache

English · [日本語](PD-CACHE-207.ja.md)

These addresses apply to the exact HW 2.3 stock build. The current-list pointer and count getters are at `0x3ee30` and `0x3ee46`. The PD context getter `0x9344` returns `0x20000004`.

| Context offset | Field |
| --- | --- |
| `+0xa8` | Current list, up to 11 words |
| `+0x4e` | SPR Source Capabilities header with NDO in bits 14–12 |
| `+0x3d5` bit 3 | EPR state flag |
| `+0x52` | EPR extended header with Data Size in the low nine bits |
| `+0xf0` | Saved pre-EPR SPR list, up to seven words |
| `+0x10c` | Saved SPR header |
| `+0x29` | Stock state byte, observed as `0x24` during detection and `0x00` after it |

EPR handling at `0x10eb4` saves the pre-EPR SPR list and replaces the current list. With the reference source, both lists matched the recorded packets. The 11 EPR and five SPR words, headers, and EPR flag remained after stock detection closed. Therefore the capture tracks ownership and freshness independently of the cached values.

```powershell
python tools/inspect_pd207.py --stock <private-stock-app.bin> --snapshot <private-snapshot-dir> --output artifacts/pd-getters-new.json
```

Use `--compare-fixture` only with the reference source. The script runs the stock getters against a private snapshot. See [capture ownership](NATIVE-CAPTURE-207.md).
