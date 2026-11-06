# KM003C PD Interrogator

[English](README.md) · 日本語

**[QR取込ページを開く](https://inuchanbt.github.io/km003c-pd-interrogator/)** · **[Windowsでの導入と復旧](docs/INSTALL.ja.md)** · **[Windows版をダウンロード](https://github.com/inuchanbt/km003c-pd-interrogator/releases)**

実機なしで試すには、取込ページで **日本語** を選び、「実機なしで試す」から電源・ケーブル・PPSのサンプルを開いてください。架空の測定値で詳細表示とJSON保存を試せます。言語を変えても同じブラウザの取込履歴が使えます。

![スマートフォン幅での電源測定デモ](docs/images/demo-en.png)

KM003CでUSB-C電源・ケーブルの情報を調べ、本体のQRからスマホへ取り込む拡張です。
対象は既知ハッシュのHW2.3／純正2.0.7です。純正の測定画面とPD処理を使い、
SPR/EPR PDOに加えてPDリビジョン、電源の識別情報、PDP、Status、メーカー情報、電源Identityを取得します。
ケーブルIdentityは純正の要求・応答を回収し、PPS Statusは既存PPS契約がある場合だけ要求します。

左右同時に約1秒長押しするとQRを開き、左右でページを切り替え、Exitで純正画面へ戻れます。
取得中は待機表示にし、完了後に固定した結果をQRにします。結果とIDは検出終了後もRAMに保持し、
再起動で消えます。取得結果がなければ `NO DATA` です。スマホ表示はSPR/EPR AVSにも対応します。

対象1個体・参照電源では、本体側の追加6要求がすべて成功し、SPR 5個・EPR 11個とともに
検出終了後も同じ結果・IDを保持することを実機確認しました。旧URLのQR5枚で、
iPhone取込・追加情報の表示・Exit後の再表示も確認済みです。
本体メニューから開始した検出でも追加6要求が成功し、今回はQR6枚となりました。
更新版では、iPhoneで6/6枚を取り込み、ケーブルIdentity欄にe-Markerシミュレーションの設定が表示されることを実機で確認しました。
PC側の情報取得・保存ログの取込・共通保存形式も含みます。

[本体の操作・取得条件](docs/NATIVE-INFO-207.ja.md)、[実機記録](docs/HARDWARE-2026-11-05.ja.md)、
[専用更新・復旧ツール](native-host/README.ja.md)、[拡張の配置](docs/OVERLAY-207.ja.md) を参照してください。
実装の基礎は [本体向けCライブラリ](native/README.ja.md)、
[PDOの固定](docs/NATIVE-CAPTURE-207.ja.md)、[PDOデコード](docs/PDO-DECODING.ja.md) に記載しています。

## 確認済みの範囲

- 69件の自動テストが通過（Cのホスト実行も含む）。通信は模擬機器で、正常応答・無応答・拒否・
  Soft Reset・切断・割り込み・USBエラー・後処理失敗を検証しています。
- Pythonとスマホ用JavaScriptで同じQRを復元し、全フィールドの一致を確認。
- QR読取ライブラリjsQRで、生成した240×240の画像を復元できることを確認。
- Chromeでデモの分割QRを逆順に入力し、2/2枚の完了と140 WのPDP情報を表示。
- 保存済みCH32ログ2件をインポートし、QR生成まで確認。
- 通常モードのKM003Cで、Revision、Source Capabilities Extended、Status、Source Info、
  Manufacturer Infoの5要求すべての応答を実機取得。SPR/EPR PDOとケーブルIdentityも保存。
  今回の電源はPD Rev 3.2 / Spec Ver 1.1、VID 32AC / PID 0022、SPR PDP 100 W / EPR PDP 240 W。
  Manufacturer Infoの応答はありますが名称欄は空白でした。Source Infoの報告PDPは1 Wという
  応答で、そのまま生値を保持しています。全電源での成功や規格適合を保証するものではありません。
- 実測結果を旧形式で3枚、標準カメラ対応のURL形式で5枚のQRへ変換し、
  元の生データ・取得状態に復元できることを確認。
  `reset` → `pdm close` 後の実測VBUSは約0.009 Vでした。
  ローカルの実測記録は `captures/stock-probe-06/`（Git管理対象外）。
  回帰テストに必要なPDパケットのみ `tests/fixtures/stock-pd32-events.json` に収録しています。
- iPhoneで、旧形式のQRを1枚ずつ画像として選択すれば全枚数を読み取れることを利用者が確認。
  公開ページでURLからの自動取込、別タブでの再開、順不同の5/5枚の復元をブラウザ検証済み。
  標準カメラからURLを開くiPhoneで、全5枚の取込成功を利用者が確認しました。
  本体版でも追加6要求の成功と、終了操作後の全結果・ID保持を実機確認しました。
- 別置きしたARMコードでも実測由来の5枚のQRを生成・復元。
  拡張の正常／欠落／破損など28ケースで、GUI入口への復帰と純正handler呼出しを確認。
  これはエミュレーター上の接続確認で、実機のGUI・PD共存試験には含めません。
- 更新ライブラリ23件・ホスト3件のテストに加え、実機で拡張の書込み・全領域照合・起動を確認。
  GUIタスクの保存済みR8が拡張入口を指すことを読み取りで観測しました。
  2段階の復元後、追加領域を含む全内部領域とloader、校正領域1024バイトが元のbackupに一致。
  利用者も純正画面・ボタン操作への復帰を確認しました。PD共存・計測精度の検証は含みません。
- 本体用QR画面は純正の画像オブジェクト・確保処理・入力グループを使用。
  非公開の純正ROM/RAMを使ったARM検証で、生成した2ページを純正デコーダーで描画し、
  スマホ用jsQRで復元。左右での循環、Exitでの操作復帰、描画間のメモリ増加がないことを確認。
  起動時の表示は取得未実施のDEMOで、実測結果を装いません。

## 起動

Python 3.10以降と、QR生成には `requirements.txt` が必要です。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

この作業環境では `pdinfo.ps1` が既存の `D:\KM003C\.venv` と、このフォルダ内の
`.deps` を利用できます。他のPCでは上記の `.venv` を作成してください。

### 実機なしでQRを試す

```powershell
.\pdinfo.ps1 demo --output artifacts/demo-new --qr
.\pdinfo.ps1 serve
```

生成されるQRは既定でURL形式です。iPhoneの標準カメラで各QRを読み、リンクを開くと
[HTTPSの取込画面](https://inuchanbt.github.io/km003c-pd-interrogator/)で自動的に取り込みます。
順番は自由です。途中でカメラへ戻って次のQRを開いても、同じ測定に追記されます。
すべて読めたら結果を確認し、「JSONを保存」または「共有する」を選びます。

デモの画像は `artifacts/demo-new/qr/qr-NN.svg`、URLテキストは `qr/pages.txt` にあります。
デモは架空のデータで、画面でもDEMOと表示します。
PCで [ローカル取込画面](http://127.0.0.1:8767/) を開き、URLを貼り付ける方法でも試せます。

スマホから試す場合は同じLANに接続し、次のように起動してPCのLANアドレスを開きます。

```powershell
.\pdinfo.ps1 serve --bind 0.0.0.0 --port 8767
```

`--import-url http://localhost:8767/` はPC上での確認用で、iPhoneからは開けません。
スマホからの通常の読み取りには既定のHTTPS公開先を使ってください。
HTTPのローカル画面ではブラウザ内カメラが使えない場合があります。
旧QRは、取込画面内のカメラ、または各QRを1枚ずつ撮影した画像の選択でも読み込めます。

途中のQRと結果は、このブラウザ内に最大10件まで自動保存します。
別タブや再読込後も再開でき、「この端末の取込履歴」から結果を切り替えられます。
「この測定を端末から削除」でその測定だけを消せます。
プライベートブラウズ、ブラウザのデータ消去、容量不足等では保持できないため、
重要な結果はJSONでも保存してください。保存できない場合は画面に案内を出します。

URLの `#` 以降に測定データを格納し、ブラウザ内で復元します。
測定データをサーバーへ送信する処理や解析サービスはありません。
取込画面はGitHub Pagesから読み込むため、ページの読み込みにはインターネット接続が必要です。

### 旧形式・別の公開先

```powershell
# 従来のKPD1テキストQR。iPhone標準カメラからは開けません。
.\pdinfo.ps1 export-qr captures/charger-01/snapshot.json --output artifacts/raw-qr --qr-format raw

# 取込画面を別のHTTPSアドレスへ配置した場合
.\pdinfo.ps1 export-qr captures/charger-01/snapshot.json --output artifacts/custom-qr --import-url https://example.com/pd/
```

URLの長さに応じて1枚当たりのデータ量と枚数を調整し、URL全体を152バイト以内に保ちます。
公開先が長すぎる場合や64枚を超える場合は出力を拒否します。
旧形式・URL形式のどちらも、取込画面と `import-qr` で復元できます。

### 公開の更新

`main` へのプッシュ後にGitHub Actionsがテストを実行し、成功した場合だけ `phone/` を
GitHub Pagesへ配置します。Pythonソース、実測ログ、ローカルの結果はWeb配置しません。

### CH32の保存ログを取り込む

```powershell
.\pdinfo.ps1 import-ch32 D:\ch32h417-pd-interrogator\captures\CAPTURE\session_0001\raw.log --output captures/ch32-import --qr
```

複数セッションを含むログでは `--session 3` のように明示してください。
再起動をまたいだ同じセッション番号の混同を避けるため、複数の起動区間が含まれるログは
起動ごとに分けてから取り込んでください。

PDO、Revision、Source Info、Statusの生値、完全な `DATA/EXT RAW8`、
対応するヘッダーに続く連番付き `EXT payload bytes` を利用します。
要約しかないメーカー名・ケーブル情報などは元のレコードとしてJSONに残します。
**完全な生データがない項目は、QR用の生データを作り足しません。**
そのため、旧ログではJSONにある情報の一部がQRに含まれないことがあります。

### 純正FWで追加情報を調べる（実験用・検証範囲限定）

既存の [KM003C CLI](https://github.com/inuchanbt/KM003C-CLI) のソースと、
同プロジェクトの実機接続用依存ライブラリが必要です。

まず手順だけ確認できます。実機への接続や出力作成は行いません。

```powershell
.\pdinfo.ps1 capture --output captures/charger-01 --dry-run
```

実機測定では測定対象の下流に機器をつながず、外部負荷をOFFにしてから実行します。
このコマンドは純正PDMを初期化し直してPD情報要求を送るため、接続状態に影響します。

```powershell
.\pdinfo.ps1 capture --km003c-root D:\KM003C --port COM14 --output captures/charger-01 --qr
```

CDCとUSBを同じ個体のシリアル番号に合わせます。初期化は
`pdm open` → `pdm set type=2,em=0,sink=1` → `entry pd` → `pd pdo`。
ケーブル模擬はOFFです。続けてRevision、Source Capabilities Extended、Status、
Source Info、Manufacturer Infoを順番に要求します。
要求順はCH32の知見に合わせ、Revisionを先頭にしています。
開始前にHID経由で測定入力の電圧・電流とCC電圧を読みます。無負荷では電源のRpだけが
見え、純正PDMがRdを有効にするまでVBUSが出ないため、初期化前はCCの接続候補も利用します。
初期化後は4〜55 VのVBUSが必要です。電流の絶対値が100 mAを超える場合は停止します。
初期化後は既定2秒間（`--native-settle`）追加要求を送らず、純正FWのEPR移行を待ちます。

今回の純正PDMはExtended Headerを自身で追加し、入力ヘッダーのNDOに応じてデータを
4バイトへゼロ埋めしました。Manufacturer InfoはTarget=0 / Ref=0の4バイト変形として
記録します。標準の2バイト要求と同一視せず、実際の送信形式を `probes` に保持します。
この電源では変形への応答を取得できました。要求種別と実送信パケットの内容が異なる場合は
未対応と判断せず停止します。

切断・新たな接続・SOP Soft Resetで取得を停止します。無応答・送信拒否・途中までの応答では
残りの要求を中止し、`reset` → `pdm close` を試みます。
後処理のコマンド応答に加えHIDでVBUSを読み、5.5 V以下を確認して結果に記録します。

PPS契約への変更、ホストによるEPR移行、VCONN Swap、能動的なケーブルIdentity要求、
ExtendedメッセージのChunk Request送信は未実装です。純正FWが処理・観測したEPR PDOや
ケーブルIdentity、分割応答は受信できた範囲で保存します。
MessageIDとGoodCRCは純正FWが扱う前提で、任意送信コマンドとの併用は実機確認が必要です。
KM003Cのログには独立したCRC/EOP検証や完全な送受信方向の記録がありません。

出力は新しいフォルダに保存し、既存の結果は上書きしません。

| 出力 | 内容 |
| --- | --- |
| `snapshot.json` | 生値・取得状態・表示用の解析結果・実行履歴 |
| `frames.jsonl` | ホスト観測時刻付きのKM003C受信フレーム |
| `events.jsonl` | PDイベント。機器時刻・生パケットを保持 |
| `qr/pages.txt` | 分割されたQRテキスト |
| `qr/qr-NN.svg` | 240×240の表示用QR画像 |
| `qr/qr-NN.bin` | 本体描画の検討用49×49・1bitのシンボル（301バイト） |

### 保存済みKM003Cイベントの再生・QR復元

```powershell
.\pdinfo.ps1 replay captures/charger-01/events.jsonl --output captures/replayed --qr
.\pdinfo.ps1 export-qr captures/charger-01/snapshot.json --output artifacts/qr-export
.\pdinfo.ps1 import-qr artifacts/qr-export/qr/pages.txt --output artifacts/restored
```

イベント再生は最初の接続区間のみ取り込み、切断・再接続・Soft Resetで止まります。
再インポートしたデータに元の測定日時は付け足しません。
各フィールドはその接続区間で最後に得た値で、同時測定値とは限りません。

## 形式と本体実装

[QR形式](docs/QR-FORMAT.ja.md) と [本体への組み込み方針](docs/FIRMWARE-INTEGRATION.ja.md) を参照。
生のJSON全体をQRへ載せず、固定長ヘッダー・TLV・CRC32・Base64urlで構成します。
機器のシリアル番号、ユーザーのメモ、ホスト日時、生ログはQR v1の対象外です。

表示名も整理しています。RevisionのVersionは **PD仕様のVersion** でありUSBバスの
バージョンではありません。Source Infoは報告／現在／最大PDPとPort Typeです。
CH32の旧表示にある温度・入力種別をそのまま引き継ぎません。
参照: [Chromium ECのRMDO・SIDO定義](https://github.com/coreboot/chrome-ec/blob/main/include/usb_pd.h)。
既存CH32リポジトリのコードや保存データは変更していません。

## テスト

```powershell
$env:PYTHONPATH = "$PWD\.deps;$PWD"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

QR画像の読取とJavaScriptとの比較テストにはNode.js、C実装の検証にはC11コンパイラーも必要です。
Clang／GCCがPATH上にない場合は、環境変数 `KPD_CC` に実行ファイルのパスを設定してください。
ARM向けの容量確認と命令実行は [native/README.md](native/README.ja.md) に記載しています。
第三者コードは [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

## ライセンス

本プロジェクト独自のコードは [MIT License](LICENSE) で提供します。
同梱のjsQRにはApache-2.0のライセンスが適用されます。
