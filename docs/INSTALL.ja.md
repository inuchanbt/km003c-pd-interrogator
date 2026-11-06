# Windowsでの導入と復旧

[English](INSTALL.md) · 日本語

対象は **POWER-Z KM003C HW 2.3／既知の純正2.0.7アプリとブートローダー**です。導入・起動・完全復元は1個体で確認しました。Windows x64、Python 3.12以降、側面HID用のデータ通信対応ケーブルを用意してください。バックアップ・更新・復旧中は測定用の両ポートを空にし、PC側のUSBポートを変えずに作業します。

## 1. ダウンロードと対応チェック

[GitHub Releases](https://github.com/inuchanbt/km003c-pd-interrogator/releases)からWindows ZIPとSHA-256を取得し、`Get-FileHash .\km003c-pd-interrogator-windows-x64.zip -Algorithm SHA256` で照合してから展開します。展開先でPowerShellを開き、純正の通常起動状態で側面HIDを接続します。

```powershell
.\km003c-backup.exe inspect
```

KM003C、HW 2.3、FW 2.0.7を確認し、表示されたシリアル番号を控えます。アプリとブートローダーのハッシュは導入前にツールが照合します。コマンドが失敗したら次へ進まず、生成したバックアップとログは非公開で保管します。

## 2. 自分の個体をバックアップ

```powershell
$stock = Join-Path $PWD 'backup'
.\km003c-backup.exe stock --serial '<自分のシリアル番号>' --output $stock
```

Exitを押しながら側面HIDを同じUSBポートに挿し直し、純正の更新画面を確認してから実行します。

```powershell
.\km003c-backup.exe dfu-check --stock $stock
.\km003c-backup.exe verify --stock $stock --require-dfu
```

## 3. 導入用と復旧用のファイルを作って照合する

```powershell
$bundle = Join-Path $PWD 'recovery'
python tools/prepare_overlay207.py --stock $stock --build overlay --output artifacts/candidate
.\km003c-overlay-host.exe --stock $stock package --candidate artifacts/candidate --output $bundle
.\km003c-overlay-host.exe --stock $stock audit --bundle $bundle
.\km003c-overlay-host.exe --stock $stock check-device --bundle $bundle --output device-check
```

`backup` と `recovery` を別途保管してから、更新画面のまま書き込みと起動を行います。各出力先には新規のフォルダー名を使います。

```powershell
.\km003c-overlay-host.exe --stock $stock flash-probe --bundle $bundle --output flash-log
.\km003c-overlay-host.exe --stock $stock start-probe --bundle $bundle --output start-log
```

## 4. 最初のQRを読む

通常起動したら測定入力へ電源を接続し、下流は空のまま純正のPD検出を実行します。左右同時に約1秒長押しし、取得中は待ちます。左右でQRを切り替え、iPhoneのカメラで各ページを開きます。全枚数が揃ったらJSONを保存・共有できます。Exitで本体の純正画面へ戻ります。

[QR取込ページ](https://inuchanbt.github.io/km003c-pd-interrogator/) · [ケーブル・PPSの操作](https://github.com/inuchanbt/km003c-pd-interrogator/blob/main/docs/NATIVE-INFO-207.ja.md)

## 純正へ戻す

測定用の両ポートを空にし、Exitを押しながら側面HIDを同じUSBポートに挿し直して純正更新画面に入ります。導入時のバックアップと復旧用ファイルを指定します。

```powershell
.\km003c-overlay-host.exe --stock $stock restore --bundle $bundle --output restore-log
.\km003c-overlay-host.exe --stock $stock start-stock --bundle $bundle --output stock-start-log
```

追加領域の消去を照合してから元のアプリを復元します。通信が中断した場合は、ログを非公開で保管して物理Exit操作でDFUに入り直し、[詳細な復旧手順](../native-host/README.ja.md)を確認してください。
