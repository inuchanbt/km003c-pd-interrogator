# KPD1 QR transfer — prototype v1

本体・PC・スマホで共有する転送形式です。変更時はPython・JavaScript・Cの互換テストを同時に更新します。

## 外側のテキスト

標準カメラ対応の既定形式は、HTTPSの取込先にKPD1ページをフラグメントとして付けたURLです。

```text
https://inuchanbt.github.io/km003c-pd-interrogator/?v=3#KPD1:<measurement-id>:<page>/<count>:<CRC32>:<base64url>
```

取込画面はフラグメントを自動解析し、同じ測定ID・枚数・CRCのページを端末内で集めます。
URLが別タブで開いても、同じオリジンのlocalStorageから再開します。
最大10測定を保持し、別の測定は混合せず別履歴に保存します。
保存が拒否された場合は画面で通知し、同じ画面内のカメラでの継続を案内します。

公開先のURLも含めて最大152 ASCIIバイトに収めます。
既定の55文字の公開先では1枚に44バイトを載せ、上限サイズは61枚です。
`?v=3` は単独ケーブル測定に対応する取込画面のバージョンです。
`?v=2` とクエリーなしの旧URL（47バイト分割）も読めます。新しい測定ではデータ量に応じて枚数が変わります。
画像のVersion・誤り訂正・ピクセル寸法は旧形式と同じです。

従来の生テキスト形式も `--qr-format raw` で生成・復元できます。

```text
KPD1:<measurement-id>:<page>/<count>:<CRC32>:<base64url>
```

- measurement-id: 8バイトの識別子を小文字16桁の16進数で表現。
- page/count: 1始まり、ゼロ埋め2桁。最大64枚。
- CRC32: 全バイナリのIEEE CRC32。大文字8桁の16進数。
- base64url: 最大81バイトの分割データ。パディングを省略した正規表現。
- 生テキストのページは最大145 ASCIIバイト。URL形式はURL全体が最大152バイトです。
- 同一内容の重複ページ・順不同を許可。測定ID・枚数・CRCの異なるページは混在拒否。
- 全枚数の取得とCRC一致まで結果を表示しません。CRCは誤り検出であり認証ではありません。

QRはVersion 8 / 誤り訂正M / byte mode。シンボル49×49、各辺4マスの白余白、
1マス4ピクセルで228×228。240×240の中央に置くため外側をさらに6ピクセル空けます。
QR表示中の余白には文字や模様を描かない構成です。ページ番号は別画面か、この外側の
限られた領域を使う設計を検討します。

参照: [DENSO WAVEの容量表](https://www.qrcode.com/en/about/versionPage/versionPage1_10.html)、
[白余白](https://www.qrcode.com/en/howto/code.html)。容量上の成立と実機での読取率は別途検証します。

## 内側のバイナリ

リトルエンディアン。圧縮や動的なJSON解釈は不要です。

| オフセット | 長さ | 意味 |
| --- | --- | --- |
| 0 | 4 | ASCII `KPD1` |
| 4 | 8 | measurement-idのバイト列 |
| 12 | 1 | origin: 0=KM003C電源測定, 1=CH32H417, 2=DEMO, 3=KM003C単独ケーブル測定 |
| 13 | 1 | capture: 0=unknown, 1=completed, 2=failed, 3=interrupted, 4=imported |
| 14以降 | 可変 | 下記のTLVをタグ1〜10について各1回 |

TLV: `tag:u8, status:u8, raw_present:u8, length:u16, raw:length bytes`。
raw_presentは0/1。0のときlengthは0。1かつlength=0は空の生データを明示します。
1フィールド最大260バイト。未知タグ、重複、欠損、末尾切断は拒否します。

| tag | 名前 | 生データ |
| --- | --- | --- |
| 1 | spr_pdos | PDOの32bit値を順番に連結 |
| 2 | epr_pdos | SPR部分・予約スロットを含む受信PDOの並び |
| 3 | revision | RMDO 4バイト |
| 4 | source_cap_ext | Source Capabilities Extendedのペイロード |
| 5 | source_info | SIDO 4バイト |
| 6 | source_status | Statusのペイロード |
| 7 | pps_status | PPS Statusのペイロード |
| 8 | manufacturer | Manufacturer Infoのペイロード |
| 9 | cable_identity | VDMヘッダーを含むSOP' Identity ACKのVDO列 |
| 10 | source_identity | VDMヘッダーを含むSOP Identity ACKのVDO列 |

statusは順に0=not_requested, 1=pass, 2=observed, 3=unsupported,
4=no_response, 5=tx_failed, 6=rejected, 7=busy, 8=reset, 9=invalid,
10=partial, 11=unknown, 12=emulation_enabled。

`pass`は対応する要求後に期待する応答を観測したことを示します。
`observed`は受信記録があることを示し、能動要求の成功や規格適合性を保証しません。
`emulation_enabled`はe-Markerシミュレーション設定（20V5A／EPR50V5A）が有効なPD検出で
回収したケーブルIdentityです。生VDOはそのまま保持し、特定の応答が実物由来かどうかまでは断定しません。
値12は末尾への追加で、既存0〜11の意味・並びは変えません。値12の読取りには更新後のデコーダーが必要です。
旧ログは要求の成功記録があっても生データが残っていない場合があります。
raw_presentを別途確認し、その場合の値は不明と扱います。

ホストの日時・シリアル・メモは転送しません。QR復元後のcaptured_atはnullです。
取得が失敗／中断した結果も転送できますが、captureと各statusを保持します。
DEMOはoriginで区別し、実測として表示しません。

`qr-NN.bin` は**QRに入れるデータではなく、生成済みのQRシンボル**です。
49×49を行優先で詰め、先頭ピクセルをバイトのMSBに置きます。
2401ビットの末尾を0で埋めて301バイト。白余白は含みません。

## 次の段階

要約専用の1枚QR、日時、測定対象の識別ラベル、報告データしか残っていない旧ログの
転送は次版の検討対象です。
本体向けCのURL／QRエンコーダーは `native/` に実装し、ホスト版との互換性と
240×240描画行の復元を確認しています。純正FWからの取得・実機QR表示・iPhone取込も確認済みです。
