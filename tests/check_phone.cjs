const assert=require('node:assert/strict');
const fs=require('node:fs');
const KPD=require('../phone/codec.js');
const jsQR=require('../phone/vendor/jsQR.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
for(const vector of input.vectors){
  const result=KPD.assemble([...vector.pages].reverse().concat(vector.pages));
  assert.deepEqual(result.snapshot.fields,vector.snapshot.fields);
  assert.equal(result.snapshot.measurement_id,vector.snapshot.measurement_id);
  assert.equal(result.snapshot.capture_status,vector.snapshot.capture_status);
  assert.equal(result.snapshot.origin,vector.snapshot.origin);
  if(vector.pages.length>1)assert.equal(KPD.assemble(vector.pages.slice(1)).snapshot,null);
  const parts=vector.pages[0].split(':');parts[4]=parts[4].slice(0,-1)+(parts[4].endsWith('A')?'B':'A');
  assert.throws(()=>KPD.assemble([parts.join(':'),...vector.pages.slice(1)]));
}
assert.throws(()=>KPD.assemble([input.vectors[0].pages[0],input.vectors[1].pages[0]]));
assert.throws(()=>KPD.parsePage('KPD1:invalid'));
for(const qr of input.images){
  const packed=Buffer.from(qr.packed,'base64'),rgba=new Uint8ClampedArray(240*240*4).fill(255);
  for(let y=0;y<49;y++)for(let x=0;x<49;x++){
    const bit=y*49+x;
    if(packed[bit>>>3]&(1<<(7-(bit&7))))for(let dy=0;dy<4;dy++)for(let dx=0;dx<4;dx++){
      const pos=(((y+4)*4+6+dy)*240+(x+4)*4+6+dx)*4;rgba[pos]=rgba[pos+1]=rgba[pos+2]=0;
    }
  }
  const result=jsQR(rgba,240,240);
  assert.ok(result,'jsQR must read the exact 240x240 device raster');assert.equal(result.data,qr.text);
}
console.log(`Phone decoder: ${input.vectors.length} Python/JS parity vectors; ${input.images.length} exact-size QR images decoded.`);
