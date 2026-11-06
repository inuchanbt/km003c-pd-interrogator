# Install and recover on Windows

English · [日本語](INSTALL.ja.md)

This experimental release targets **POWER-Z KM003C HW 2.3 with the exact supported stock 2.0.7 application and bootloader**. These tools have been exercised on one meter. The checks below determine whether your unit matches the supported build.

Use Windows x64, Python 3.12 or later, and a data-capable cable for the side HID port. Disconnect the source and all downstream equipment from the two measurement ports during backup, installation, and recovery. Keep the meter on the same physical PC USB port throughout.

## 1. Download and check compatibility

Download the Windows ZIP and its SHA-256 file from [GitHub Releases](https://github.com/inuchanbt/km003c-pd-interrogator/releases). Compare the digest before extracting.

```powershell
Get-FileHash .\km003c-pd-interrogator-windows-x64.zip -Algorithm SHA256
```

Extract the ZIP and open PowerShell in its folder. Connect the side HID port normally while the meter runs untouched stock firmware.

```powershell
.\km003c-backup.exe inspect
```

Check for KM003C, HW 2.3, and firmware 2.0.7. Take note of the serial number for your next command. The tools check both SHA-256 digests before installation. See the supported [application digest](STOCK-207.md) and [bootloader digest](OVERLAY-207.md). Stop if a command fails. The `backup`, `candidate`, `recovery`, and log directories below must be new when created. Keep the backup and recovery files private.

## 2. Qualify your own stock backup

```powershell
$stock = Join-Path $PWD 'backup'
.\km003c-backup.exe stock --serial '<your serial>' --output $stock
```

Hold Exit while reconnecting the side HID cable to the same USB port. Check that the meter shows its stock update screen. Then verify the loader and application in DFU.

```powershell
.\km003c-backup.exe dfu-check --stock $stock
.\km003c-backup.exe verify --stock $stock --require-dfu
```

## 3. Prepare, audit, and install

The ZIP contains the project's prebuilt overlay pieces and host tools. Build the install and recovery files locally using your qualified backup. Python uses only its standard library for this step.

```powershell
$bundle = Join-Path $PWD 'recovery'
python tools/prepare_overlay207.py --stock $stock --build overlay --output artifacts/candidate
.\km003c-overlay-host.exe --stock $stock package --candidate artifacts/candidate --output $bundle
.\km003c-overlay-host.exe --stock $stock audit --bundle $bundle
.\km003c-overlay-host.exe --stock $stock check-device --bundle $bundle --output device-check
```

Copy the backup and recovery folders to a safe place. With the meter still in DFU, install and then start the overlay.

```powershell
.\km003c-overlay-host.exe --stock $stock flash-probe --bundle $bundle --output flash-log
.\km003c-overlay-host.exe --stock $stock start-probe --bundle $bundle --output start-log
```

## 4. Scan your first measurement

After normal startup, connect a USB-C source to the measurement input with downstream equipment still disconnected. Run the stock PD detection. Hold Left and Right together for about one second. Wait if the meter shows **READING PD**. Use Left and Right to switch QR pages and Exit to close the display. On your phone, scan and open each link. After the importer shows all pages, choose **Save JSON** or **Share**.

[Open the QR importer](https://inuchanbt.github.io/km003c-pd-interrogator/) · [Cable and PPS instructions](https://github.com/inuchanbt/km003c-pd-interrogator#measure-and-scan)

## Return to stock

Disconnect both measurement ports. Hold Exit while reconnecting the side HID cable to the same physical USB port. Reuse the original qualified backup and recovery bundle.

```powershell
.\km003c-overlay-host.exe --stock $stock restore --bundle $bundle --output restore-log
.\km003c-overlay-host.exe --stock $stock start-stock --bundle $bundle --output stock-start-log
```

The restore command clears the added sectors before applying the original application. After a USB error or interrupted transfer, save the logs privately, re-enter DFU using the physical Exit button, and follow the [detailed recovery notes](../native-host/README.md).
