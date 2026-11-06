# 本体向け処理の移植用ライブラリ

[English](README.md) · 日本語

`kpd.c` は測定結果 → KPD1 → HTTPS URL → QR → RGB565描画行を作るC11実装です。
**ライブラリ単体は書き込み用アプリではありません。**
PD送受信、メニュー、ボタン、液晶ドライバーは含みません。

## データと処理の境界

1. 取得タスクが10項目の生バイト・状態・8バイトの測定IDを用意する。
2. 取得側の排他中に `kpd_freeze()` で転送専用バッファへコピーする。
3. 排他を解放し、GUI側で `kpd_encode_page()` を呼ぶ。
4. `kpd_qr_row()` の各行を、純正のGUI／バス管理に従って描く。
5. ボタンで1始まりのページ番号を変更する。同じtransferを最後まで使用する。

取得バッファを後から更新しても、固定済みのQRは変わりません。
QR生成中に取得側の排他を保持しないでください。PDの割り込み／タスクからは呼びません。
純正GUIのflush関数を任意タスクから直接呼ぶ実装も含めていません。
GUIとの排他、LVGLの完了通知、実機でのPD処理時間は接続時に確認が必要です。

既定URLはスマホで確認済みのGitHub Pagesに固定しています。
44バイトずつ最大61ページ。Version 8/M・byte mode・誤り訂正の自動昇格なしです。
行データは240画素の白黒RGB565、49×49シンボルを4倍、外周22画素を白にします。
ページ番号などをQRの白余白に重ねないでください。

ヒープ確保、ファイル操作、時刻取得、USB、フラッシュ／校正領域へのアクセスはありません。
`kpd_transfer` は2,672バイト、`kpd_workspace` は758バイト、行バッファは480バイトです。
これらの合計3,910バイトに、入力ビューと呼出スタック、GUI側の管理領域が加わります。
置き場所は純正FW側のメモリ管理を確認してから確保します。

## 検証

全体のテストにはPython・Node.js・C11コンパイラー（ClangまたはGCC）が必要です。
コンパイラーがPATH上にない場合は、`KPD_CC` に実行ファイルのパスを指定します。

```powershell
python -m unittest discover -s tests -v
```

C実装をホスト用共有ライブラリとして実行し、PythonのTLV／URLと比較します。
元データを変更した後も固定コピーが保たれること、最大長、空応答、異常入力も確認します。
生成したRGB565の全行を画像としてjsQRで読み取り、URLに戻します。

ARM向けビルドは次の独立した容量確認です。Clang/LLDを使います。
容量は生成ELFの割当対象セクションから計算するため、別のsizeツールは不要です。

```powershell
python tools/check_native_size.py --clang C:\path\to\clang.exe
```

出力は `artifacts/native-size/sizecheck-only.elf`。起動コード・ベクター・KM003Cのメタデータを
持たない**容量確認専用ファイル**です。書き込みツールに渡すものではありません。
`freestanding/include/` の最小標準ヘッダーと `runtime.c` はオフラインのARMビルド用です。
`sizecheck/runtime.c` は容量確認専用の呼出し口とバッファです。
実際の移植では移植先のCライブラリ／SDKを使います。

Unicorn 2.1.4を追加すると、ARM命令そのものの動作も確認できます。

```powershell
python tools/check_native_arm.py artifacts/native-size/sizecheck-only.elf
```

確認済みのClang 23.1.4 / Cortex-M4 Thumb / `-Oz` では、容量確認用コードも含めて
text 5,152バイト、data 0、bss 4,308バイトです。
ARMエミュレーター上でも実測由来の全5ページがPythonと一致し、描画行を読めました。
この値は実機上の処理時間やPDとの共存を示すものではありません。

純正側の調査結果は [STOCK-207.md](../docs/STOCK-207.ja.md) を参照してください。

内部フラッシュへ別置きする接続試作を `overlay207/` に追加しました。
264バイトの判定処理と別領域の拡張をリンクし、純正GUIへの呼び出し・復帰、
欠落／破損時の切替をARM命令で確認しています。
`native-host/` の専用ツールを使い、実機の接続試作と純正への完全復元も確認しました。
QR画面と本体の追加GETは実機確認済みです。取得条件については
[NATIVE-INFO-207.md](../docs/NATIVE-INFO-207.ja.md) を参照してください。
配置、検証コマンド、復旧計画は [OVERLAY-207.md](../docs/OVERLAY-207.ja.md) に記載しています。
