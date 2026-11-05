# PDOの表示用デコード

PythonとスマホのJavaScriptは、受信した生値を保持したまま表示用の値を計算します。
本体のQR形式・ページ数・CRC・ファームウェアを変更するものではありません。

## AVS APDO

PDOのtypeがAugmentedで、subtypeが1ならEPR AVS、2ならSPR AVSです。
未対応のsubtype 3はraw表示を維持します。ゼロの予約スロットも位置を保持します。

| 種類 | デコードする情報 |
| --- | --- |
| SPR AVS | bit19..10: 9〜15 V帯の最大電流、bit9..0: 15〜20 V帯の最大電流。いずれも10 mA単位 |
| EPR AVS | bit25..17: 最大電圧、bit15..8: 最小電圧。いずれも100 mV単位。bit7..0: PDP、1 W単位 |
| 両方 | bit27..26のpeak current codeを数値コードとして保持。アンペア値には変換しない |

一次資料として、[Linux USB PDヘッダーの固定版](https://github.com/torvalds/linux/blob/9277b3b2d5a990591e36ec391fe14682e460ab94/include/linux/usb/pd.h)
のSPR/EPR AVS APDO定義と対応する仕様表番号を照合しています。

今回の生値:

- `E404B1F4`: SPR AVS、9〜15 V帯は3 A、15〜20 V帯は5 A。
- `D7C096F0`: EPR AVS、15〜48 V、PDP 240 W。

EPR AVSには固定の最大電流フィールドがありません。スマホ画面では、電圧・PDPとは別に
最大電圧時のPDP換算値を表示します。今回なら240 W ÷ 48 V = 5 Aです。
これを全電圧域に共通の電流フィールドとしてJSONに追加しません。
SPRの電流帯が0なら、その帯は「非対応（0 A）」と表示します。

## 保存済みデータへの反映

スマホの履歴はデコード済みJSONではなくQR原文を保持しており、表示時に組み立て直します。
取込画面を再読込みして同じ履歴を選べば、既存のQRを再撮影せず新しい表示へ更新できます。
更新時に古い表示コードが残らないよう、codecとappのscript URLも版付きにしています。

実測2つのAVS値、9bitのEPR最大電圧、SPRの0 A帯、未対応subtype、Python/JSの一致、
表示文言、履歴再読込、実寸QRの復元をテストしています。
