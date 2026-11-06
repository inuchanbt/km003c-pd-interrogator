# 純正2.0.7のPDO保存領域

[English](PD-CACHE-207.md) · 日本語

対象は既知ハッシュのHW2.3／純正2.0.7です。2026-11-05、同じ電源・下流負荷なしで
純正のPD検出を1回実行し、通常GUIと共存するRAM上の保存場所を調べました。
**この調査自体は保存領域の特定です。** その後に追加した、本体のコピーへ固定してQRへ渡す処理は
[NATIVE-CAPTURE-207.md](NATIVE-CAPTURE-207.ja.md) に記載しています。

## 確認した位置

純正コンソールの `pd pdo` とGUIのPDO表示に相当する処理から追跡しました。
現在一覧のgetterは `0x3ee30`、個数getterは `0x3ee46` です。
所有者のROM/RAMを使ったARM実行でも、下表の値を返すことを確認しました。
呼出し時にはThumb bitを付与します。

| 内容 | 位置／処理 |
| --- | --- |
| PDコンテキスト | getter `0x9344` が `0x20000004` を返す |
| 現在のPDO生値 | コンテキスト `+0xa8`、最大11個×4バイト |
| SPR Source Capabilities header | `+0x4e`。NDOはbit12..14 |
| EPR状態のフラグ | `+0x3d5` のbit3。これだけでは新しいデータの証明にならない |
| EPR extended header | `+0x52`。生バイト数は下位9bit、個数はその値を4で割る |
| EPR前のSPR生値 | `+0xf0`、最大7個×4バイト。getter `0x3ee6e` |
| 保存SPR header | `+0x10c`。個数getter `0x3ee78` |
| 純正の状態値 | `+0x29`。今回の検出中は `0x24`、終了後は `0x00` |

純正のEPR処理 `0x10eb4` は、元のSPRを保存してから現在一覧をEPRで置き換えます。
これにより、予約スロットを含む11個のEPR PDOと、5個の元のSPR PDOを区別できます。
ヘッダーにあるPD revisionのbitを、GET_Revisionの32bit応答へ作り替えてはいけません。

## 実機での観測

| 段階 | 純正状態 | 現在の一覧 | 保存SPR |
| --- | --- | --- | --- |
| 側面HIDだけ | `0x00` | 空 | 空 |
| 電源だけ接続、検出操作前 | `0x00` | 空 | 空 |
| 純正PD検出中 | `0x24` | EPR 11個 | SPR 5個 |
| `reset` → `pdm close` 後 | `0x00` | **11個が残る** | **5個が残る** |

今回のSPR/EPRの生バイト列は、以前の同一電源の実測パケットを公開fixtureへ整理した
`tests/fixtures/stock-pd32-events.json` の両フィールドと完全一致しました。
検出中VBUSは約5.093 V、解除後は約0.008 Vでした。設定は既存の
`pdm open` → `pdm set type=2,em=0,sink=1` → `entry pd` → `pd pdo` です。
新しいGET要求や電圧の個別指定、フラッシュ書込みは行っていません。

**終了後も個数、ヘッダー、EPRフラグ、生値が残ります。**
そのため、QRを開く際にこの保存領域だけを読んで「現在の電源」として出す方法は不十分です。
本体の取得開始・終了を管理し、取得中の結果を世代付きで固定してから保持する接続が必要です。
未検証の状態番号を同じ意味と見なしたり、未取得の追加情報を補完したりしません。
接続変更をまたぐ保持、同時更新との排他、測定IDの生成と追加GET要求の接続も残っています。

## 再現可能なオフライン照合

`inspect-gui` で保存した非公開の完全SRAMと、元のqualified ROMを使用します。

```powershell
python tools/inspect_pd207.py --stock <private-stock-app.bin> --snapshot <private-snapshot-dir> --output artifacts/pd-getters-new.json
```

`--compare-fixture` は今回の参照電源についてだけ付けます。別の電源には使用しません。
ARMで4つの純正getterを実行し、候補のオフセット／個数と一致することを確認します。
検出中と解除後の両snapshotで、同じPDOが返ることを実行確認しています。
ツールは診断JSONを作るだけで、これを新しい実測やQRとして出力しません。
入力・出力に非公開のRAM由来データを含むため、`artifacts/` の外へ公開しません。

読み取りは非原子的です。この解析ではRTOS、PD割り込み、抜き差し中の競合を再現していません。
ローカル記録は `artifacts/pd207-native-session/{active,released}/`、
取得と解除の記録は同ディレクトリの `session.json` に保持しています。
