# Third-party notices

## Native updater dependencies

The source preparer retrieves, without vendoring private data:

- [okhsunrog/km003c-rs](https://github.com/okhsunrog/km003c-rs/tree/efed50556d7376914bffd510e012aaa98081936f),
  commit `efed50556d7376914bffd510e012aaa98081936f`, MIT OR Apache-2.0.
  The isolated `hw23-overlay` patch retains its authentication, AES and update transport;
  default firmware bounds remain unchanged. Original licenses are retained in the prepared source.
- [inuchanbt/apdo-doom-on-km003c](https://github.com/inuchanbt/apdo-doom-on-km003c/tree/20622200644f02c6a5e48ad664dc7b8b6fb236ce/bootstrap),
  commit `20622200644f02c6a5e48ad664dc7b8b6fb236ce`, MIT.
  Only its independent backup crate and license are prepared for the native host.

These sources stay in ignored `.deps/native-host/`. The patch, source preparer and
Cargo lockfiles are tracked. Private recovery files and compiled host binaries are not distributed.

## Nayuki QR Code generator (C)

`native/vendor/qrcodegen/qrcodegen.c` and `qrcodegen.h` are unmodified from
[Project Nayuki](https://github.com/nayuki/QR-Code-generator/tree/bcfe1b018cec5f1f91b5705eca5bb4cdd34e9f8d/c),
commit `bcfe1b018cec5f1f91b5705eca5bb4cdd34e9f8d`.
The MIT license and copyright notice remain in both source files.

- qrcodegen.c SHA-256: `6a2b9cc65176f2345dde260c74b6d352627e8a0a6385d086ae0e9c5d0913c70c`
- qrcodegen.h SHA-256: `e82df4bff37d18b5863b9e7486fe6bda1b6cda8c3b9ecebfec473907265cb589`

## Offline analysis tools

The optional stock/ARM analysis scripts use separately installed Unicorn 2.1.4
and Capstone 5.0.7. Their code is not vendored. See their distributions for their
licenses. Stock firmware, derived RAM/disassembly, device identity and backups
are not distributed with this repository.

## jsQR 1.4.0

`phone/vendor/jsQR.js` is the browser QR decoder from
[cozmo/jsQR](https://github.com/cozmo/jsQR), distributed as npm package `jsqr@1.4.0`.
Its Apache-2.0 license is retained in `phone/vendor/jsQR.LICENSE`.

Retrieved from `https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js`.
SHA-256: `bc40c8a15196236b2314db0856f72ca0b49980cd5413b8c852a7349f5fee0859`.
It is served locally with the importer; the importer does not load CDN resources.

## Python qrcode 8.2

The QR generator depends on [python-qrcode](https://github.com/lincolnloop/python-qrcode)
through `requirements.txt`. It is installed separately; its source is not vendored
in this project. See the package's BSD license distributed with the installation.

## KM003C CLI

The experimental live adapter imports the user's separate
[KM003C CLI checkout](https://github.com/inuchanbt/KM003C-CLI) at runtime.
No source is copied or relicensed here. Its transport dependencies and licenses
remain the responsibility of that checkout's installation.

## Protocol references

RMDO and SIDO field layouts were checked against Chromium EC's
[`include/usb_pd.h`](https://github.com/coreboot/chrome-ec/blob/main/include/usb_pd.h).
The implementation retains raw values and does not copy the tentative
temperature/input or USB-version labels from older CH32 logs.
