/* KPD1 portable decoder. No network requests and no evaluation of QR contents. */
(function (root) {
  'use strict';
  const fields = ['spr_pdos','epr_pdos','revision','source_cap_ext','source_info',
    'source_status','pps_status','manufacturer','cable_identity','source_identity'];
  const states = ['not_requested','pass','observed','unsupported','no_response',
    'tx_failed','rejected','busy','reset','invalid','partial','unknown','emulation_enabled'];
  const origins = ['km003c','ch32h417','demo','km003c_cable','km003c_pps'];
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
    if(text.length>2048)throw Error('QRテキストが長すぎます');
    if(/^https?:\/\//i.test(text)){
      let url;try{url=new URL(text);}catch{throw Error('取込URLが不正です');}
      if(!url.hostname||url.username||url.password||!url.hash)throw Error('取込URLが不正です');
      try{text=decodeURIComponent(url.hash.slice(1));}catch{throw Error('取込URLのデータが壊れています');}
    }
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
  function decodeCable(words, origin) {
    const headerless=origin==='km003c_cable' && words.length===4;
    if(!headerless && (words.length<5 || words.length>7 ||
        ((words[0]&0xFFFF87DF)>>>0)!==0xFF008041))return null;
    const major=headerless?1:(words[0]>>>13)&3;
    const offset=headerless?0:1;
    const [ident,,product,vdo]=words.slice(offset,offset+4);
    const kind={3:'passive',4:'active'}[(ident>>>27)&7];
    if(!kind || !vdo || major>1)return null;
    const legacy=major===0,version=legacy?null:(vdo>>>21)&7;
    if(!legacy && !(kind==='passive'?[0]:[1,2,3]).includes(version))return null;
    const vbus=kind==='passive' || !!(vdo&0x10);
    const voltageCode=legacy?null:(vdo>>>9)&3;
    const voltage=legacy || !vbus?null:voltageCode===3?50000:20000;
    const currentCode=(vdo>>>5)&3,current=vbus?([null,3000,5000,null][currentCode]):null;
    const epr=!legacy && (vdo&0x20000)?(voltage===50000 && current===5000?true:null):false;
    const idHex=value=>value.toString(16).toUpperCase().padStart(4,'0');
    return {kind,vid:idHex(ident&65535),pid:idHex(product>>>16),
      layout:headerless?'pd3_assumed':legacy?'pd2':'pd3',vdo_version_code:version,
      vbus_through:vbus,max_vbus_mv:voltage,voltage_code:voltageCode,
      current_code:currentCode,current_ma:current,epr_capable:epr,speed_code:vdo&7};
  }
  function describe(name, bytes, origin) {
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
        else if(p>>>30===3 && (p>>>28&3)===1) Object.assign(row,{kind:'epr_avs',min_mv:(p>>>8&255)*100,max_mv:(p>>>17&511)*100,pdp_w:p&255,peak_current_code:p>>>26&3});
        else if(p>>>30===3 && (p>>>28&3)===2) Object.assign(row,{kind:'spr_avs',max_current_9v_15v_ma:(p>>>10&1023)*10,max_current_15v_20v_ma:(p&1023)*10,peak_current_code:p>>>26&3});
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
    if(name==='pps_status') {
      const result={};
      let offset=0;
      if(origin==='km003c_pps'){
        if(bytes.length!==4 && bytes.length!==8)return null;
        const request=u32(0);
        Object.assign(result,{requested_mv:((request>>>9)&2047)*20,requested_ma:(request&127)*50});
        offset=4;
      }
      if(bytes.length===offset+4){
        const voltage=u16(offset),current=bytes[offset+2],flags=bytes[offset+3];
        if(!(flags&0xF1))Object.assign(result,{reported_mv:voltage===65535?null:voltage*20,
          reported_ma:current===255?null:current*50,temperature_code:(flags>>>1)&3,
          current_limit:!!(flags&8),flags});
      }
      return Object.keys(result).length?result:null;
    }
    if(['cable_identity','source_identity'].includes(name)) {
      if(bytes.length%4) throw Error('VDOの長さが不正です');
      const words=[]; for(let i=0;i<bytes.length;i+=4) words.push(u32(i));
      const result={vdos:words.map(word=>word.toString(16).toUpperCase().padStart(8,'0'))};
      const cable=name==='cable_identity' && decodeCable(words,origin);
      if(cable)result.cable=cable;
      return result;
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
      if(present) {field.raw=hex(raw);const decoded=describe(name,raw,result.origin);if(decoded!==null) field.decoded=decoded;}
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
  function formatPDO(p) {
    if(p.kind==='fixed')return `${p.index}: ${p.voltage_mv/1000} V / ${p.current_ma/1000} A`;
    if(p.kind==='pps')return `${p.index}: PPS ${p.min_mv/1000}–${p.max_mv/1000} V / ${p.current_ma/1000} A`;
    if(p.kind==='spr_avs'){
      const current=ma=>ma?`${ma/1000} A`:'非対応（0 A）';
      return `${p.index}: SPR AVS\n  9–15 V / ${current(p.max_current_9v_15v_ma)}\n  15–20 V / ${current(p.max_current_15v_20v_ma)}`;
    }
    if(p.kind==='epr_avs'){
      const equivalent=p.max_mv>0?`\n  ${p.max_mv/1000} V時のPDP換算: ${Number((p.pdp_w*1000/p.max_mv).toFixed(3))} A`:'';
      return `${p.index}: EPR AVS ${p.min_mv/1000}–${p.max_mv/1000} V / ${p.pdp_w} W MAX${equivalent}`;
    }
    return `${p.index}: ${p.kind} ${p.raw}`;
  }
  function formatCable(cable) {
    const {kind,vid,pid,layout,vbus_through,max_vbus_mv,voltage_code,
      current_ma,current_code,epr_capable,speed_code}=cable;
    const voltage=max_vbus_mv===null?'不明':`${max_vbus_mv/1000} V${voltage_code===1||voltage_code===2?'（旧コード）':''}`;
    const current=current_ma!==null?`${current_ma/1000} A`:
      kind==='active' && current_code===0?'USB既定電流':'不明';
    const epr=epr_capable===null?'判定不可（VDOの値が不整合）':epr_capable?'対応':'非対応';
    const speeds=layout==='pd2'?['USB 2.0のみ','USB 3.1 Gen 1','USB 3.1 Gen 2']:
      ['USB 2.0のみ','USB 3.2 Gen 1','USB 3.2 / USB4 Gen 2','USB4 Gen 3','USB4 Gen 4'];
    const rows=[
      kind==='passive'?'パッシブケーブル':'アクティブケーブル',
      `VID ${vid} / PID ${pid}`,
      ...(vbus_through?[`VBUS定格 ${voltage}`,`対応電流 ${current}`]:['VBUS配線なし']),
      `EPR ${epr}`,
      `USB速度区分 ${speeds[speed_code]??'不明'}`,
    ];
    if(layout==='pd3_assumed')rows.push('純正画面のVDOをPD 3.x形式として解釈');
    return rows.join('\n');
  }
  const api={parsePage,assemble,unpack,crc32,formatPDO,formatCable};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  root.KPD=api;
})(typeof globalThis!=='undefined'?globalThis:this);
