# 対象個体向けの更新・復旧ツール

[English](README.md) · 日本語

HW2.3／純正2.0.7の既知アプリと、既知ハッシュのブートローダーだけに対応する実験用CLIです。
2026-11-05に対象1個体で、別置き拡張の書込み・起動・GUI呼出し先の観測・純正への完全復元を確認しました。
別置き拡張は、純正PDM検出中のSPR/EPR PDOを保持しQRへ渡します。
追加GETの実装と確認範囲は [本体の追加情報取得](../docs/NATIVE-INFO-207.ja.md) を参照してください。
[実機記録](../docs/HARDWARE-2026-11-05.ja.md) と [配置](../docs/OVERLAY-207.ja.md) を参照してください。

## 固定ソースの準備とビルド

Python 3.12、Rust/Cargo（検証済み1.99.1、依存元の最低要件1.97）が必要です。

```powershell
python tools/prepare_native_host.py --fetch
cargo build --manifest-path native-host/Cargo.toml --locked
```

今回のWindows GNU環境ではLLDでリンクし、実機操作の前に `--help` の正常終了を確認します。
GNU ldで作った実行ファイルでは、Windows APIのimport解決により起動時に停止する現象を確認しました。
GNU環境の準備後、既存の `CARGO_TARGET_X86_64_PC_WINDOWS_GNU_RUSTFLAGS` に
`-C link-arg=-fuse-ld=lld` を追加してビルドします。LLDはPATH上に必要です。

ソースが手元にある場合は、ダウンロードの代わりに次を使えます。
指定リポジトリの作業中の変更は取り込まず、固定コミットをGit archiveで取得します。

```powershell
python tools/prepare_native_host.py --km003c-source C:\src\km003c-rs --backup-source C:\src\apdo-doom-on-km003c
```

ソースは `.deps/native-host/` にだけ展開します。通常のcheckoutやCargoキャッシュは変更しません。
`km003c-lib` は `efed50556d7376914bffd510e012aaa98081936f`、backup crateは
`20622200644f02c6a5e48ad664dc7b8b6fb236ce` に固定しています。
通常の `FirmwareImage::parse` と上限 `0x6fffe` は維持します。
ローカルfeature `hw23-overlay` に、既知loader、純正コードの変更位置、拡張ヘッダー／CRC、
最大2追加セクターを検査する専用経路を加えています。
転送直前にも、実機のHWとloader全バイトを再読取して確認します。
暗号化と更新通信は固定ライブラリの既存処理を使います。

専用イメージの最大長は `0x74000`（`0x4000` 起点、消去終了は最大 `0x78000`）。
旧版の `0x76000` 境界を宣言するbundleも、実データがその範囲内の場合だけ検査・置換対象にできます。
AESの余白も最小限の16バイト境界への切上げに限定し、余白の復号値は全てFFである必要があります。
追加ブロックや、モデル外の余白データを転送しません。

ローカルパッチを変更した場合だけ、準備済み領域を明示的に更新できます。

```powershell
python tools/prepare_native_host.py --refresh-patch
```

## オフラインでパッケージを作る

前段の `prepare_overlay207.py` 出力と、この個体のqualified backupを使います。
以下のパスは例です。出力は必ず新規の非公開フォルダーにします。

```powershell
$hostTool = '.\native-host\target\debug\km003c-overlay-host.exe'
$stockBackup = 'C:\private\qualified-stock-backup'
& $hostTool --stock $stockBackup package --candidate artifacts\overlay207-private --output artifacts\overlay207-bundle
& $hostTool --stock $stockBackup audit --bundle artifacts\overlay207-bundle
```

作成物は拡張用、追加領域の消去用、元のrestore container、およびmanifestです。
元のrestore containerはバックアップからそのまま保持し、元のアプリ全バイトを再現できることを確認します。
全内部領域 `0x4000..0x7ffff` に対する2段階の復旧モデルも照合します。
`.mencrypt`・期待値・読み戻し・TCB／スタックの記録には非公開データが含まれるため公開しません。

## 読み取り専用の実機照合

```powershell
& $hostTool --stock $stockBackup check-device --bundle artifacts\overlay207-bundle --output artifacts\device-check-new
```

通常モード／手動DFUのどちらでも、qualified backupと同じ物理USBポートを対象にします。
loaderと全内部領域を照合し、通常モードでは校正領域1024バイトも照合します。
このコマンドはモード切替・フラッシュ書込み・PD送信をしません。

既知の通常アプリに対する `inspect-gui --bundle ... --output ...` は、上の照合に続けて
SRAM全体と純正LVGLヒープを非公開ファイルへ読み出します。実行中の非原子的な観測で、
画面・タスク・機器識別情報などが含まれるため、出力を公開しません。
このデータは [QR GUIのオフライン検証](../docs/NATIVE-GUI-207.ja.md) に使います。

## 実機へ適用し、純正へ戻す

測定用の両コネクターを空にし、側面HIDだけを接続します。
最初はExitを押しながらHIDを挿し直し、純正の更新画面へ入ります。
この個体用の復旧パッケージが揃い、上のオフライン照合が成功している必要があります。

```powershell
& $hostTool --stock $stockBackup flash-probe --bundle artifacts\overlay207-bundle --output artifacts\flash-new
& $hostTool --stock $stockBackup start-probe --bundle artifacts\overlay207-bundle --output artifacts\start-new
```

`flash-probe` は元の純正全バイトとの一致を要求し、書込み後もDFUに留まります。
試作間を更新する場合だけ `--previous-bundle <以前のbundle>` を明示できます。
新旧両bundleを同じqualified backupで監査し、実機の全内部領域が旧試作の期待値に
完全一致する場合だけ更新します。不明なアプリを上書きする許可にはなりません。
同じ試作が既に入っている場合は書込みを繰り返しません。
全内部領域・loader・書込み範囲外の一致を確認してから、別の `start-probe` で起動します。
起動後も内部全領域を読み、GUI TCBの保存済みR8が拡張の入口に一致することを観測します。
この観測は呼出し先選択の確認であり、QR画面表示やPD共存の検証ではありません。

正常に動いている既知の試作からは、次の順にDFUへ戻して完全復元できます。

```powershell
& $hostTool --stock $stockBackup enter-dfu --bundle artifacts\overlay207-bundle --output artifacts\dfu-new
& $hostTool --stock $stockBackup restore --bundle artifacts\overlay207-bundle --output artifacts\restore-new
& $hostTool --stock $stockBackup start-stock --bundle artifacts\overlay207-bundle --output artifacts\stock-start-new
```

`restore` は、追加セクターを消すerase passを照合してから、元のrestore containerを適用します。
元の状態に一致していれば書き直さず、検証済みのerase pass状態なら最終段階から進みます。
中断等の未知バイトは、loaderと書込み範囲外が元と同じ場合に限り、明示的な `restore` で上書きできます。
正常なアプリ以外からのDFU復帰は、物理Exit操作を使います。

通信やプログラムの成否が不明な場合、データブロックの再送も、自動復旧も行いません。
失敗後は更なるメモリ要求も送りません。native loaderが暗号化データを待っている可能性があるためです。
証拠を保存し、側面HIDを挿し直して物理DFUへ入り直してから、実機の状態に応じて復旧します。

## テスト

```powershell
cargo test --manifest-path native-host/Cargo.toml --locked
cargo test --manifest-path .deps/native-host/km003c-rs/km003c-lib/Cargo.toml --features hw23-overlay --lib firmware:: --locked
```

ホスト側3件、ライブラリの既存・専用経路23件を確認しています。
通常上限維持、対象違い・loader違いでの停止、確認応答の破損・切断・空／過剰データ、
再送禁止、拡張コードの変更範囲、暗号化余白、消去範囲と2段階復旧を検証します。
