const assert = require('node:assert/strict');
const fs = require('node:fs');
const jsQR = require('../phone/vendor/jsQR.js');
const images = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const image of images) {
  const raw = Buffer.from(image.rgb565, 'base64');
  assert.equal(raw.length, 240 * 240 * 2);
  const rgba = new Uint8ClampedArray(240 * 240 * 4).fill(255);
  for (let p = 0; p < 240 * 240; ++p) {
    assert.equal(raw[p * 2], raw[p * 2 + 1]);
    const value = raw[p * 2];
    assert.ok(value === 0 || value === 255);
    rgba[p * 4] = rgba[p * 4 + 1] = rgba[p * 4 + 2] = value;
  }
  const result = jsQR(rgba, 240, 240);
  assert.ok(result, 'phone must decode the actual C-generated RGB565 display rows');
  assert.equal(result.data, image.text);
}
console.log(`${images.length} native C rasters decoded`);
