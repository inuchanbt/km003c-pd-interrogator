# Exact-build Windows host tools

English · [日本語](README.ja.md)

Use the [installation and recovery guide](../docs/INSTALL.md) for the prebuilt Windows ZIP. The host tools qualify an exact HW 2.3 / stock 2.0.7 backup, package and audit the overlay offline, check the connected meter, and perform explicit installation or restoration commands.

## Build from source

Use Python 3.12 or later and the Rust 1.99.1 toolchain. The preparer retrieves the pinned km003c-rs and backup sources and applies the isolated `hw23-overlay` patch. It writes into `.deps/native-host/`.

```powershell
python tools/prepare_native_host.py --fetch
cargo build --manifest-path native-host/Cargo.toml --locked
cargo test --manifest-path native-host/Cargo.toml --locked
```

Use `--km003c-source` and `--backup-source` for local checkouts of the pinned commits. On the tested Windows GNU toolchain, link with LLD and run both tools with `--help` before packaging. The ordinary updater bounds remain in place outside this dedicated exact-build path.

For a Windows ZIP, build both release executables. The backup CLI manifest is `.deps/native-host/backup/bootstrap/Cargo.toml`. Build the stock-free overlay using [the overlay build instructions](../docs/OVERLAY-207.md). Export `cargo metadata --format-version 1 --locked --filter-platform x86_64-pc-windows-gnu` for each manifest. Run `python tools/package_windows_release.py --help` for the packaging inputs. The packager uses a fixed file list and writes a separate SHA-256 file. Audit a fresh extraction with a qualified backup before distributing it.

## Validation and recovery

Use your unit's qualified backup for `package`, `audit`, and all device commands. The bundle includes the extended update, a separate erase pass for the added sectors, and the original restore container. The host compares expected bytes and models both restoration stages before programming. Output folders can contain firmware, identity, calibration, or RAM and are for private storage.

`check-device` reads and compares the device without switching mode or writing. `flash-probe` requires the known stock image or an exact, explicitly supplied prior bundle. It verifies the full affected ranges and stays in DFU. `start-probe` independently checks the installed image before startup. `restore` checks and clears the added sectors before applying the original application. `start-stock` verifies the original image before startup.

After an ambiguous transfer error, keep the evidence and re-enter DFU physically with Exit held during HID reconnect. The tool avoids automatic data retries while the loader could be waiting for more encrypted bytes. Refer to the [detailed record of command arguments and state checks](README.ja.md) for developer diagnostics.
