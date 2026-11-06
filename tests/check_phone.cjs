const assert=require('node:assert/strict');
const fs=require('node:fs');
const KPD=require('../phone/codec.js');
const jsQR=require('../phone/vendor/jsQR.js');
const {Store,STORAGE_KEY,LIMIT}=require('../phone/store.js');
require('../phone/samples.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const english=en=>en;
const sampleSource=KPD.assemble(KPDSamples.source).snapshot;
assert.equal(sampleSource.origin,'demo');
assert.match(KPD.formatPDO(sampleSource.fields.epr_pdos.decoded[10],english),/PDP equivalent at 48 V: 5 A/);
const sampleCable=KPD.assemble(KPDSamples.cable).snapshot;
assert.equal(sampleCable.origin,'km003c_cable');
assert.match(KPD.formatCable(sampleCable.fields.cable_identity.decoded.cable,english),/Passive cable[\s\S]+USB4 Gen 3/);
const samplePps=KPD.assemble(KPDSamples.pps).snapshot;
assert.equal(samplePps.origin,'km003c_pps');
assert.equal(samplePps.fields.pps_status.decoded.requested_mv,5100);
assert.equal(samplePps.fields.pps_status.decoded.reported_ma,0);
assert.throws(()=>KPD.parsePage('bad'),error=>error.message.includes('supported KPD1') && error.ja.includes('KPD1形式'));
for(const vector of input.vectors){
  const result=KPD.assemble([...vector.pages].reverse().concat(vector.pages));
  assert.deepEqual(result.snapshot.fields,vector.snapshot.fields);
  assert.equal(result.snapshot.measurement_id,vector.snapshot.measurement_id);
  assert.equal(result.snapshot.capture_status,vector.snapshot.capture_status);
  assert.equal(result.snapshot.origin,vector.snapshot.origin);
  assert.deepEqual(KPD.assemble(vector.url_pages).snapshot.fields,vector.snapshot.fields);
  if(vector.pages.length>1)assert.equal(KPD.assemble(vector.pages.slice(1)).snapshot,null);
  const parts=vector.pages[0].split(':');parts[4]=(parts[4].startsWith('A')?'B':'A')+parts[4].slice(1);
  assert.throws(()=>KPD.assemble([parts.join(':'),...vector.pages.slice(1)]),/checksum does not match/);
}
assert.throws(()=>KPD.assemble([input.vectors[0].pages[0],input.vectors[1].pages[0]]));
assert.throws(()=>KPD.parsePage('KPD1:invalid'));
const storage={data:new Map(),getItem(key){return this.data.get(key)||null;},setItem(key,value){this.data.set(key,value);}};
const vector=input.vectors.at(-1),first=new Store(storage);
const actual=KPD.assemble(vector.url_pages).snapshot.fields;
assert.equal(KPD.formatPDO(actual.spr_pdos.decoded[4]),'5: SPR AVS\n  9–15 V / 3 A\n  15–20 V / 5 A');
assert.equal(KPD.formatPDO(actual.epr_pdos.decoded[10]),'11: EPR AVS 15–48 V / 240 W MAX\n  48 V時のPDP換算: 5 A');
const measuredSource=input.vectors.find(v=>v.snapshot.measurement_id==='0000000000000021');
const measuredFields=KPD.assemble(measuredSource.url_pages).snapshot.fields;
assert.equal(measuredFields.source_status.decoded.trailing_hex,'00');
assert.equal(measuredFields.source_identity.decoded.identity.dfp_type,'power_brick');
assert.equal(KPD.formatPDO(measuredFields.spr_pdos.decoded[2]),'3: Battery 5–12 V / 最大 45.25 W');
assert.equal(KPD.formatPDO(measuredFields.spr_pdos.decoded[3]),'4: Variable 16–20 V / 最大 2.5 A');
assert.match(KPD.formatPDO({index:1,kind:'spr_avs',max_current_9v_15v_ma:2500,max_current_15v_20v_ma:0}),/非対応（0 A）/);
assert.ok(!KPD.formatPDO({index:1,kind:'epr_avs',min_mv:15000,max_mv:0,pdp_w:100}).includes('Infinity'));
assert.equal(KPD.formatPDO({index:6,kind:'reserved',raw:'00000000'},english),'6: Reserved slot 0x00000000');
assert.equal(KPD.formatPDO({index:5,kind:'raw',raw:'F0001234'}),'5: 不明なPDO 0xF0001234');
first.add([vector.url_pages[0]]);assert.equal(first.current().received,1);
const second=new Store(storage);assert.equal(second.current().received,1);
second.add(vector.url_pages.slice(1));assert.deepEqual(second.current().snapshot.fields,vector.snapshot.fields);
const reopened=new Store(storage);
assert.match(KPD.formatPDO(reopened.current().snapshot.fields.spr_pdos.decoded[4]),/SPR AVS/);
assert.match(KPD.formatPDO(reopened.current().snapshot.fields.epr_pdos.decoded[10]),/240 W MAX/);
assert.equal(reopened.current().snapshot.fields.source_status.decoded.present_input.external_type,'ac');
assert.deepEqual(first.sync().snapshot.fields,vector.snapshot.fields);
second.add([KPD.parsePage(vector.url_pages[0]).text]);assert.equal(second.current().received,vector.url_pages.length);
const original=storage.getItem(STORAGE_KEY),parts=KPD.parsePage(vector.url_pages[0]).text.split(':');
parts[4]=(parts[4].startsWith('A')?'B':'A')+parts[4].slice(1);
assert.throws(()=>second.add([parts.join(':')]),/same number but different contents/);
assert.equal(storage.getItem(STORAGE_KEY),original);
second.add(input.vectors[0].pages);assert.equal(second.list().length,2);
second.select(KPD.parsePage(vector.url_pages[0]).key);assert.deepEqual(second.current().snapshot.fields,vector.snapshot.fields);
second.clear();assert.equal(second.current().received,0);assert.equal(second.list().length,1);
const cable=input.vectors.find(v=>v.snapshot.origin==='km003c_cable');
function cableOf(id){
  const measurementId=id.toString(16).padStart(16,'0');
  const vector=input.vectors.find(v=>v.snapshot.measurement_id===measurementId);
  return KPD.assemble(vector.pages).snapshot.fields.cable_identity.decoded.cable;
}
const standalone=KPD.assemble(cable.url_pages).snapshot.fields.cable_identity.decoded.cable;
assert.deepEqual([standalone.max_vbus_mv,standalone.current_ma,standalone.epr_capable,standalone.speed_code],[50000,5000,false,0]);
for(const label of ['VBUS定格 50 V','対応電流 5 A','EPR 非対応','USB速度区分 USB 2.0のみ'])assert.ok(KPD.formatCable(standalone).includes(label));
assert.equal(cableOf(9).epr_capable,true);
assert.match(KPD.formatCable(cableOf(9)),/USB4 Gen 4/);
assert.deepEqual([cableOf(10).max_vbus_mv,cableOf(10).epr_capable],[null,false]);
assert.match(KPD.formatCable(cableOf(10)),/USB 3.1 Gen 2/);
assert.deepEqual([cableOf(11).vbus_through,cableOf(11).current_ma],[false,null]);
assert.match(KPD.formatCable(cableOf(11)),/VBUS配線なし/);
assert.deepEqual([cableOf(12).voltage_code,cableOf(12).max_vbus_mv],[1,20000]);
assert.match(KPD.formatCable(cableOf(13)),/EPR 判定不可.*\nUSB速度区分 不明/);
assert.equal(cableOf(14),undefined);
assert.equal(cableOf(15),undefined);
assert.equal(cableOf(16).epr_capable,true);
second.add(cable.url_pages);second.add(vector.url_pages);
const mixedHistory=new Store(storage);
for(const item of [cable,vector]){
  const record=mixedHistory.list().find(entry=>entry.id===item.snapshot.measurement_id);
  assert.equal(record.origin,item.snapshot.origin);
  assert.deepEqual(mixedHistory.select(record.key).snapshot.fields,item.snapshot.fields);
}
const unavailable=new Store({getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}});
unavailable.add(vector.url_pages);assert.equal(unavailable.persisted,false);assert.deepEqual(unavailable.current().snapshot.fields,vector.snapshot.fields);
unavailable.clear();assert.equal(unavailable.list().length,0);
storage.setItem(STORAGE_KEY,'not json');assert.equal(new Store(storage).list().length,0);
const limitStorage={...storage,data:new Map()},limited=new Store(limitStorage);
const sample=input.vectors[0].pages;
for(let i=0;i<LIMIT+2;i++){const id=i.toString(16).padStart(16,'0');limited.add([sample[0].replace(/KPD1:[0-9a-f]{16}:/,`KPD1:${id}:`)]);}
assert.equal(limited.list().length,LIMIT);assert.equal(limited.current().received,1);
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
