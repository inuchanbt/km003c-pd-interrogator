# USB PDの表示用デコード

[English](PDO-DECODING.md) · 日本語

PythonとスマホのJavaScriptは、受信した生値を保持したまま表示用の値を計算します。
本体のQR形式・ページ数・CRC・ファームウェアを変更するものではありません。

## 仕様と表示の対応

表番号は参照電源が報告した USB PD Revision 3.2 Version 1.1 に基づきます。PD 2.0 の Cable VDO は Chromium EC の Rev 2.0 定義とも照合しました。
[USB-IF USB PD 3.2 v1.1 配布ファイル](https://usb.org/sites/default/files/usb_pd_r3.2_v1.1.zip) · [USB-IF USB PD 3.2 v1.2 配布ファイル](https://www.usb.org/sites/default/files/USB_PD_R3.2_V1.2%283%29_0.zip) · [Chromium EC VDO 定義](https://chromium.googlesource.com/chromiumos/platform/ec/+/master/include/usb_pd_vdo.h)

| 項目 | 仕様と実装 |
| --- | --- |
| Source Status | §6.5.2.1 表6.55。SDBのオフセット0は温度、1は現在の入力、2はバッテリービットマップ、3は保護イベントとPPS動作モード、4は温度状態、5は電力制限、6は電源状態とLED指示。 |
| SOP Discover Identity ACK | §6.4.4.2–3、表6.29、6.33および6.37–40。Structured Discover Identity ACKのヘッダーを確認し、ID Header、Cert Stat、Product、製品種別VDOを分ける。USB Host/Device、Modal、UFP/DFP種別、端子、VID/PID、XID、製品BCDを保持する。 |
| Source Capabilities Extended | §6.5.1 表6.54。VID、PID、XID、FW/HW版は先頭10バイト。SPR PDPはオフセット23、EPR PDPはオフセット24。EPRのバイトがない旧データも表示する。 |
| Source Info | §6.4.11 表6.51。1語のSIDOでB7…0はReported PDP、B15…8はPresent PDP、B23…16はMaximum PDP（1 W単位）。B31はポート種別、B30…24は予約。 |
| Fixed Source PDO | §6.4.1.2 表6.9。B19…10は50 mV、B9…0は10 mA単位。B29…23の能力情報は先頭の5 V Fixedだけに適用する。ピーク電流 B21…20はコードで表示し、B22は予約として保持する。 |
| Battery / Variable Source PDO | §6.4.1.2 表6.11–12。B31…30の `01` はBattery、`10` はVariable。B29…20は上限電圧、B19…10は下限電圧（各50 mV単位）。B9…0はBatteryなら最大電力（250 mW単位）、Variableなら最大電流（10 mA単位）。 |
| PPS APDO | 表6.13。電圧の上下限は100 mV単位、電流は50 mA単位。Power Limitedビットと予約ビットは生値で保持する。 |
| Cable Identity | §6.4.4.3.1 表6.41–42およびPD 2.0のCable VDO。HW/FW版 B31…24、相手側端子 B19…18、レイテンシ B16…13、VCONN終端 B12…11。Active Cable VDO 1の版1.3ではSBU B8…7とSOP″コントローラー B3も表示する。 |

### 取得値と判定の範囲

- Statusは6バイトの旧データと、電源状態バイトを含む7バイトの定義済み範囲を読む。5バイト以下は長さ不足。8バイト目以降は、KPD1にExtended HeaderとData Sizeがないため用途を確定せず、生値を残す。温度と温度状態のコード0は非対応で、イベントと電力制限の各ビット0は該当フラグなし。PPSモードビットはPPS動作時だけ有効。
- SOP Identityの製品種別VDOは、SVDM版とID Headerの役割から判定する。両データロールがある場合は UFP VDO、ゼロのパッド、DFP VDO の順を確認する。確認済みの UFP VDO 1.3 と DFP VDO 1.2 を展開し、未対応版・種別や配置を確定できない語は生値で表示する。旧SVDM 1.0の共通項目は読める。XIDの取得値から認証状態は判定しない。
- Fixed先頭PDOのB24（非分割拡張メッセージ）とB23（EPRモード）はビット値をJSONに保持し、画面では電源が報告したPDリビジョンに応じてPD 3.0以降／3.1以降の意味を表示する。Revision未取得時はビット値を表示する。報告リビジョンは個々のパケットのヘッダーとは別の情報。USBサスペンドビットは契約成立後かつUSB通信対応時に意味を持つ。
- 単独ケーブル測定の4語はVDMヘッダーがないため、PD 3.xの推定として表示する。レイテンシは報告コードからの区分。Active Cable VDO 2や未対応のVDO版は生値で保持する。シミュレーション設定が有効な記録には従来どおりその状態を併記する。
- USB PD 3.2 v1.2では、SIDO2を含む2語のSource Infoも定義されている。現行のデコーダーは1語形式に対応し、それ以外の長さは生データとして保持する。

### 実測回帰データ

| 生データ | 期待する読み取り |
| --- | --- |
| `0006000000000000` | 外部AC、内部温度と温度状態は非対応、保護イベントと出力制限のフラグはなし。電源状態は非対応、末尾 `00` は未解釈。 |
| `FF00A841 01C032AC 00000000 00220000 40000000` | SVDM 2.1、VID `32AC` / PID `0022`、DFP Power Brick、USB Type-Cレセプタクル、DFP VDO 1.2。 |
| `01F0F080` | Reported PDP 1 W、Present / Maximum PDP 240 W、保証型ポート。報告値をそのまま表示する。 |
| `1C6020C2 00000000 0A140310 460A2643` | 単独ケーブルの4語。PD 3.x推定で50 V、5 A、EPR対応、USB4 Gen 3、レイテンシ10 ns未満。 |

このほか、実測SCEDBのSPR 100 WとEPR 240 W、および元の5枚組QRをテストに使用する。
PDOは仕様から独立に作った期待値をPythonとJavaScriptで照合する。予約ビットを意図的に立てたベクトルで、隣接フィールドのデコードへの影響を確認する。DRDのパッド、Active Cable、予約コードと長さ不足にも合成ベクトルを使う。

## AVS APDO

PDOのtypeがAugmentedで、subtypeが1ならEPR AVS、2ならSPR AVSです。
未対応のsubtype 3は生値を表示します。EPRリストの1〜7番にはSPR PDOと未使用のゼロスロットが入り、EPR PDOは8番から始まります（§6.5.15.1）。詳細表示では元の番号とゼロスロットを確認できます。

| 種類 | デコードする情報 |
| --- | --- |
| SPR AVS | bit19..10: 9〜15 V帯の最大電流、bit9..0: 15〜20 V帯の最大電流。いずれも10 mA単位 |
| EPR AVS | bit25..17: 最大電圧、bit15..8: 最小電圧。いずれも100 mV単位。bit7..0: PDP、1 W単位 |
| 両方 | bit27..26のpeak current codeを数値コードとして保持。アンペア値には変換しない |

上記は表6.14–15に基づきます。SPR AVSの電流フィールドはLinuxの定義、EPRのスロット位置はChromium ECの実装とも照合しました。
[Linux USB PDの定義](https://github.com/torvalds/linux/blob/master/include/dt-bindings/usb/pd.h) · [Chromium ECのEPRスロット処理](https://chromium.googlesource.com/chromiumos/platform/ec/+/da5a30aac716d8d9f1c5faf8f029ce75fccdb910/common/usb_pd_dual_role.c#87)

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
