/* Browser-local page storage. No measurement data is sent to a server. */
(function(root){
  'use strict';
  const KPD=typeof module!=='undefined'&&module.exports?require('./codec.js'):root.KPD;
  const STORAGE_KEY='km003c-pd-interrogator:transfers:v1',LIMIT=10;
  const empty=()=>({version:1,active:null,transfers:{}});
  class Store{
    constructor(storage){this.storage=storage;this.persisted=!!storage;this.state=this.read();}
    read(){
      if(!this.storage)return empty();
      try{
        const raw=this.storage.getItem(STORAGE_KEY);
        if(!raw)return empty();
        if(raw.length>150000)throw Error('Stored state is too large');
        const parsed=JSON.parse(raw),result=empty();
        if(parsed.version!==1||!parsed.transfers||typeof parsed.transfers!=='object')return result;
        for(const [key,entry]of Object.entries(parsed.transfers).slice(0,LIMIT)){
          if(!entry||!Array.isArray(entry.pages)||!entry.pages.length||entry.pages.length>64||!Number.isFinite(entry.updatedAt))continue;
          try{
            const pages=entry.pages.map(text=>KPD.parsePage(text));
            if(pages.some(page=>page.key!==key))continue;
            KPD.assemble(pages.map(page=>page.text));
            result.transfers[key]={pages:[...new Set(pages.map(page=>page.text))],updatedAt:entry.updatedAt};
          }catch{/* Ignore corrupt records without interpreting them as measurements. */}
        }
        result.active=Object.hasOwn(result.transfers,parsed.active)?parsed.active:null;
        return result;
      }catch{this.persisted=false;return empty();}
    }
    write(){
      if(!this.storage){this.persisted=false;return false;}
      try{this.storage.setItem(STORAGE_KEY,JSON.stringify(this.state));this.persisted=true;return true;}
      catch{this.persisted=false;return false;}
    }
    add(texts){
      const pages=texts.filter(text=>text.trim()).map(text=>KPD.parsePage(text));
      if(!pages.length)throw KPD.localizedError("Scan a QR code or enter QR text.",'QRを読み込むか、QRテキストを入力してください');
      const key=pages[0].key;
      if(pages.some(page=>page.key!==key))throw KPD.localizedError("Import one measurement at a time.",'別の測定のQRは1つずつ取り込んでください');
      const latest=this.read(),previous=latest.transfers[key]||this.state.transfers[key];
      const combined=[...(previous?.pages||[]),...pages.map(page=>page.text)];
      const result=KPD.assemble(combined); // Validate conflicts and CRC before persisting.
      const canonical=[...new Set(combined)].sort((a,b)=>KPD.parsePage(a).index-KPD.parsePage(b).index);
      const transfers={...this.state.transfers,...latest.transfers,[key]:{pages:canonical,updatedAt:Date.now()}};
      const keys=[key,...Object.keys(transfers).filter(k=>k!==key).sort((a,b)=>transfers[b].updatedAt-transfers[a].updatedAt)];
      this.state={version:1,active:key,transfers:Object.fromEntries(keys.slice(0,LIMIT).map(k=>[k,transfers[k]]))};
      this.write();return result;
    }
    current(){const record=this.state.transfers[this.state.active];return record?KPD.assemble(record.pages):{received:0,total:0,snapshot:null};}
    select(key){if(!Object.hasOwn(this.state.transfers,key))throw KPD.localizedError("The saved measurement could not be found.",'保存済みの測定が見つかりません');this.state.active=key;this.write();return this.current();}
    list(){
      return Object.entries(this.state.transfers).sort((a,b)=>b[1].updatedAt-a[1].updatedAt).map(([key,entry])=>{
        const page=KPD.parsePage(entry.pages[0]),result=KPD.assemble(entry.pages);
        return {key,id:page.id,received:result.received,total:result.total,origin:result.snapshot?.origin};
      });
    }
    clear(){const latest=this.read();if(!this.persisted)latest.transfers={...this.state.transfers};const key=this.state.active;delete latest.transfers[key];latest.active=null;this.state=latest;this.write();}
    sync(){const active=this.state.active;this.state=this.read();if(Object.hasOwn(this.state.transfers,active))this.state.active=active;return this.current();}
  }
  const api={Store,STORAGE_KEY,LIMIT};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.KPDStore=api;
})(typeof globalThis!=='undefined'?globalThis:this);
