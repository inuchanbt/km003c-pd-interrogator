# Third-party notices

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
