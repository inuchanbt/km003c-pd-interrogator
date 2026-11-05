/* KPD1 portable decoder. No network requests and no evaluation of QR contents. */
(function (root) {
  'use strict';
  const fields = ['spr_pdos','epr_pdos','revision','source_cap_ext','source_info',
    'source_status','pps_status','manufacturer','cable_identity','source_identity'];
  const states = ['not_requested','pass','observed','unsupported','no_response',
    'tx_failed','rejected','busy','reset','invalid','partial','unknown'];
  const origins = ['km003c','ch32h417','demo'];
  const captureStates = ['unknown','completed','failed','interrupted','imported'];
  const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2,'0')).join('').toUpperCase();
  function crc32(bytes) {
    let value = 0xFFFFFFFF;
    for (const byte of bytes) {
      value ^= byte;
      for (let i=0;i<8;i++) value = (value >>> 1) ^ ((value & 1) ? 0xEDB88320 : 0);
    }
    return ((value ^ 0xFFFFFFFF) >>> 0).toString(16).toUpperCase().padStart(8,'0');
  }
  function parsePage(text) {
    if (typeof text !== 'string') throw Error('QRテキストを入力してください');
    text = text.trim();
    const m = /^KPD1:([0-9a-f]{16}):([0-9]{2})\/([0-9]{2}):([0-9A-F]{8}):([A-Za-z0-9_-]+)$/.exec(text);
    if (!m || text.length > 152) throw Error('対応するKPD1形式ではありません');
    const [,id,part,count,crc,encoded] = m, index=Number(part), total=Number(count);
    if (!(1<=index && index<=total && total<=64)) throw Error('ページ番号が不正です');
    let binary;
    try { binary = atob(encoded.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-encoded.length%4)%4)); }
    catch { throw Error('ページの文字列が壊れています'); }
    const bytes = Uint8Array.from(binary, c=>c.charCodeAt(0));
    if (!bytes.length || bytes.length>81) throw Error('ページの長さが不正です');
    if(btoa(binary).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')!==encoded) throw Error('ページの文字列が正規形式ではありません');
    return {id,index,total,crc,bytes,text,key:`${id}:${total}:${crc}`};
  }
  function describe(name, bytes) {
    const v = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    const u16 = i=>v.getUint16(i,true), u32=i=>v.getUint32(i,true);
    if (['spr_pdos','epr_pdos'].includes(name)) {
      if (bytes.length%4) throw Error('PDOの長さが不正です');
      const rows=[];
      for(let i=0;i<bytes.length;i+=4) {
        const p=u32(i), row={index:i/4+1,raw:p.toString(16).toUpperCase().padStart(8,'0'),type:p>>>30};
        if(!p) row.kind='reserved';
        else if(p>>>30===0) Object.assign(row,{kind:'fixed',voltage_mv:(p>>>10&1023)*50,current_ma:(p&1023)*10});
        else if(p>>>30===3 && (p>>>28&3)===0) Object.assign(row,{kind:'pps',min_mv:(p>>>8&255)*100,max_mv:(p>>>17&255)*100,current_ma:(p&127)*50});
        else row.kind='raw';
        rows.push(row);
      }
      return rows;
    }
    if(name==='revision' && bytes.length===4) {
      const p=u32(0);
      return {pd_revision:`${p>>>28}.${p>>>24&15}`,pd_spec_version:`${p>>>20&15}.${p>>>16&15}`,reserved:p&65535};
    }
    if(name==='source_info' && bytes.length===4) return {port_reported_pdp_w:bytes[0],port_present_pdp_w:bytes[1],port_maximum_pdp_w:bytes[2],port_type:bytes[3]>>>7,reserved:bytes[3]&127};
    if(name==='source_cap_ext' && bytes.length>=24) return {vid:u16(0).toString(16).toUpperCase().padStart(4,'0'),pid:u16(2).toString(16).toUpperCase().padStart(4,'0'),xid:u32(4).toString(16).toUpperCase().padStart(8,'0'),fw_version:bytes[8],hw_version:bytes[9],spr_pdp_w:bytes[23],epr_pdp_w:bytes.length>=25?bytes[24]:null};
    if(name==='manufacturer' && bytes.length>=4) return {vid:u16(0).toString(16).toUpperCase().padStart(4,'0'),pid:u16(2).toString(16).toUpperCase().padStart(4,'0'),name:new TextDecoder().decode(bytes.slice(4)).split('\0',1)[0].replace(/ +$/,'')};
    if(['cable_identity','source_identity'].includes(name)) {
      if(bytes.length%4) throw Error('VDOの長さが不正です');
      const vdos=[]; for(let i=0;i<bytes.length;i+=4) vdos.push(u32(i).toString(16).toUpperCase().padStart(8,'0'));
      return {vdos};
    }
    return null;
  }
  function unpack(bytes) {
    if(bytes.length<14 || hex(bytes.slice(0,4))!=='4B504431' || bytes[12]>=origins.length || bytes[13]>=captureStates.length) throw Error('データ形式が不正です');
    const result={schema:'pdinfo/1',measurement_id:hex(bytes.slice(4,12)).toLowerCase(),origin:origins[bytes[12]],capture_status:captureStates[bytes[13]],captured_at:null,fields:{}};
    const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength), seen=new Set();
    for(let pos=14;pos<bytes.length;) {
      if(pos+5>bytes.length) throw Error('データが途中で切れています');
      const tag=bytes[pos],state=bytes[pos+1],present=bytes[pos+2],length=v.getUint16(pos+3,true);pos+=5;
      if(tag<1||tag>fields.length||seen.has(tag)||state>=states.length||present>1||length>260||(!present&&length)||pos+length>bytes.length) throw Error('データ項目が不正です');
      seen.add(tag);const name=fields[tag-1],field={status:states[state]},raw=bytes.slice(pos,pos+length);pos+=length;
      if(present) {field.raw=hex(raw);const decoded=describe(name,raw);if(decoded!==null) field.decoded=decoded;}
      result.fields[name]=field;
    }
    if(seen.size!==fields.length) throw Error('データ項目が不足しています');
    return result;
  }
  function assemble(texts) {
    let identity=null;const chunks=new Map();
    for(const text of texts) {
      const page=parsePage(text);
      if(identity && identity.key!==page.key) throw Error('別の測定のQRが混ざっています。「やり直す」で切り替えてください');
      identity=page;
      if(chunks.has(page.index) && hex(chunks.get(page.index))!==hex(page.bytes)) throw Error('同じ番号のページ内容が一致しません');
      chunks.set(page.index,page.bytes);
    }
    if(!identity) return {received:0,total:0,snapshot:null};
    if(chunks.size!==identity.total) return {received:chunks.size,total:identity.total,snapshot:null};
    const size=Array.from(chunks.values()).reduce((a,b)=>a+b.length,0),bytes=new Uint8Array(size);let offset=0;
    for(let i=1;i<=identity.total;i++) {const chunk=chunks.get(i);bytes.set(chunk,offset);offset+=chunk.length;}
    if(crc32(bytes)!==identity.crc) throw Error('データのチェックサムが一致しません。QRを読み直してください');
    const snapshot=unpack(bytes);
    if(snapshot.measurement_id!==identity.id) throw Error('測定IDが一致しません');
    return {received:chunks.size,total:identity.total,snapshot};
  }
  const api={parsePage,assemble,unpack,crc32};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  root.KPD=api;
})(typeof globalThis!=='undefined'?globalThis:this);
