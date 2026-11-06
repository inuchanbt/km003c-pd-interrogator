'use strict';
const $=id=>document.getElementById(id);
let browserStorage=null;try{browserStorage=window.localStorage;}catch{/* In-page scanning still works without persistence. */}
const transfers=new KPDStore.Store(browserStorage);
let snapshot=null,stream=null,scanFrame=null;
const names={spr_pdos:'SPR PDO',epr_pdos:'EPR PDO',revision:'PDリビジョン',source_cap_ext:'電源の識別情報',source_info:'電源のPDP情報',source_status:'電源ステータス',pps_status:'PPSステータス',manufacturer:'メーカー情報',cable_identity:'ケーブルIdentity',source_identity:'電源Identity'};
const statuses={not_requested:'未要求',pass:'取得成功',observed:'受信を記録',unsupported:'非対応の応答',no_response:'無応答',tx_failed:'送信失敗',rejected:'拒否',busy:'待機応答',reset:'リセット',invalid:'取得失敗',partial:'一部のみ取得',unknown:'不明',emulation_enabled:'e-Markerシミュレーション有効時の記録'};
const measurementNames={km003c:'電源測定',km003c_cable:'ケーブル測定',km003c_pps:'PPS測定',ch32h417:'CH32H417',demo:'DEMO'};
function report(error){$('error').textContent=error?.message||String(error||'');}
function addPages(texts){
  try{
    transfers.add(texts);showCurrent();report('');return true;
  }catch(error){report(error);}
  return false;
}
function showCurrent(){
  const result=transfers.current();
  $('counter').textContent=result.total?`${result.received} / ${result.total} 枚`:'0 枚';
  $('progress').max=result.total||1;$('progress').value=result.received;
  $('progressText').firstElementChild.textContent=result.snapshot?'すべて読み込みました':result.total?'続きのQRを読み込んでください':'読込待ち';
  $('nextStep').textContent=result.snapshot?'結果を確認して保存・共有できます。':result.total?'iPhoneのカメラに戻り、次のQRのリンクを開いてください。この画面のカメラでも続けられます。':'標準カメラでURL形式のQRを読むと、自動で取り込みます。';
  $('storageNotice').textContent=transfers.persisted?'':'このブラウザでは途中結果を保存できません。この画面のカメラで続きのQRを読み、完了後にJSONを保存してください。';
  const list=transfers.list();$('historyCard').classList.toggle('hidden',!list.length);$('history').replaceChildren();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='保存済みの測定を選ぶ';$('history').append(placeholder);
  for(const item of list){const option=document.createElement('option');option.value=item.key;const label=measurementNames[item.origin];option.textContent=`${label?label+' · ':''}${item.id} · ${item.received}/${item.total} 枚${item.received===item.total?' · 完了':''}`;$('history').append(option);}
  $('history').value=transfers.state.active||'';
  snapshot=result.snapshot;$('result').classList.toggle('hidden',!snapshot);
  if(snapshot)render();
}
function render(){
  $('result').classList.remove('hidden');$('badges').replaceChildren();$('rows').replaceChildren();
  const cable=snapshot.origin==='km003c_cable';
  const pps=snapshot.origin==='km003c_pps';
  for(const text of [measurementNames[snapshot.origin],`ID ${snapshot.measurement_id}`]){const badge=document.createElement('span');badge.className='badge';badge.textContent=text;$('badges').append(badge);}
  const warning=snapshot.origin==='demo'?'デモ用の架空データです。実際の測定結果ではありません。':['failed','interrupted','unknown'].includes(snapshot.capture_status)?`取得状態: ${snapshot.capture_status}。一部の情報が取得できていない可能性があります。`:'';
  $('notice').textContent=warning;$('notice').classList.toggle('hidden',!warning);
  for(const [name,field]of Object.entries(snapshot.fields)){
    if((cable && name!=='cable_identity') || (pps && name!=='pps_status'))continue;
    const tr=document.createElement('tr'),th=document.createElement('th'),td=document.createElement('td');
    th.scope='row';th.textContent=names[name];
    const d=field.decoded;let text=statuses[field.status]||field.status;
    if(!field.raw && field.status==='pass')text='応答成功（このQRに生データなし）';
    if(!field.raw && field.status==='partial')text='一部のみ取得（完全な生データなし）';
    if(d){
      if(Array.isArray(d))text=d.map(KPD.formatPDO).join('\n');
      else if(name==='revision')text=`Rev ${d.pd_revision} / Spec Ver ${d.pd_spec_version}`;
      else if(name==='source_info')text=`報告 ${d.port_reported_pdp_w} W / 現在 ${d.port_present_pdp_w} W / 最大 ${d.port_maximum_pdp_w} W`;
      else if(name==='source_cap_ext')text=`VID ${d.vid} / PID ${d.pid}\nSPR ${d.spr_pdp_w} W${d.epr_pdp_w===null?'':` / EPR ${d.epr_pdp_w} W`}`;
      else if(name==='manufacturer')text=d.name||`名称は空欄 / VID ${d.vid} / PID ${d.pid}`;
      else if(name==='pps_status'){
        const value=(number,unit)=>number===null?'報告なし':`${number/1000} ${unit}`;
        const lines=[];
        if(d.requested_mv!==undefined)lines.push(`要求値 ${value(d.requested_mv,'V')} / ${value(d.requested_ma,'A')}`);
        if(d.reported_mv!==undefined){
          lines.push(`電源の応答 ${value(d.reported_mv,'V')} / ${value(d.reported_ma,'A')}`);
          lines.push(d.current_limit?'電流制限中':'定電圧動作');
          if(d.temperature_code)lines.push(`温度状態 ${['','正常','警告','過温度'][d.temperature_code]}`);
        }
        text=lines.join('\n');
      }
      else if(name==='cable_identity'){
        text=d.cable?KPD.formatCable(d.cable):`${d.vdos.length} VDOを保存（ケーブルの仕様は不明）`;
        if(cable){
          const labels=['IDヘッダー','認証情報','製品情報','ケーブルVDO'];
          if(d.vdos.length>=5)labels.unshift('VDMヘッダー');
          const detail=d.vdos.map((vdo,index)=>`${labels[index]||'追加VDO'}  0x${vdo}`).join('\n');
          if(detail)text+=`\n\n${detail}`;
        }
      }
      else text=`${d.vdos.length} VDOを保存`;
      text=field.status==='emulation_enabled'?`${statuses[field.status]}\n${text}`:`${text}\n${statuses[field.status]}`;
    }else if(field.raw)text+=`\n${field.raw}`;
    td.textContent=text;td.style.whiteSpace='pre-line';tr.append(th,td);$('rows').append(tr);
  }
  $('raw').textContent=JSON.stringify(snapshot,null,2);
}
$('add').onclick=()=>{addPages($('pages').value.split(/\r?\n/));};
$('reset').onclick=()=>{transfers.clear();$('pages').value='';showCurrent();report('');stopCamera();};
$('history').onchange=()=>{if(!$('history').value)return;try{transfers.select($('history').value);showCurrent();report('');}catch(error){report(error);}};
$('files').onchange=async event=>{
  try{
    for(const file of event.target.files){
      if(file.size>20*1024*1024)throw Error('画像は20 MB以下にしてください');
      if(file.type.startsWith('image/')){
        const url=URL.createObjectURL(file),image=new Image();
        try{image.src=url;await image.decode();const scale=Math.min(1,2048/Math.max(image.width,image.height));const canvas=document.createElement('canvas');canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);const context=canvas.getContext('2d',{willReadFrequently:true});context.fillStyle='white';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);const data=context.getImageData(0,0,canvas.width,canvas.height);const code=jsQR(data.data,data.width,data.height);if(!code)throw Error(`${file.name}: QRを見つけられませんでした`);addPages([code.data]);}
        finally{URL.revokeObjectURL(url);}
      }else{if(file.size>10000)throw Error('QRテキストファイルが大きすぎます');addPages((await file.text()).split(/\r?\n/));}
    }
  }catch(error){report(error);}finally{event.target.value='';}
};
function stopCamera(){if(scanFrame)cancelAnimationFrame(scanFrame);scanFrame=null;if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;$('video').srcObject=null;$('video').classList.add('hidden');$('stopCamera').classList.add('hidden');}
$('camera').onclick=async()=>{
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw Error('この接続ではカメラを使えません。QR画像を選ぶか、QRテキストを貼り付けてください。');
    stopCamera();stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment',width:{ideal:640}},audio:false});const video=$('video');video.srcObject=stream;video.classList.remove('hidden');$('stopCamera').classList.remove('hidden');await video.play();report('');
    const canvas=document.createElement('canvas');let previous='',last=0;
    function scan(now){
      if(!stream)return;
      if(video.readyState>=2 && now-last>200){last=now;canvas.width=video.videoWidth;canvas.height=video.videoHeight;const context=canvas.getContext('2d',{willReadFrequently:true});context.drawImage(video,0,0);const data=context.getImageData(0,0,canvas.width,canvas.height),code=jsQR(data.data,data.width,data.height);if(code&&code.data!==previous){previous=code.data;addPages([code.data]);}}
      scanFrame=requestAnimationFrame(scan);
    }scanFrame=requestAnimationFrame(scan);
  }catch(error){stopCamera();report(error);}
};
$('stopCamera').onclick=stopCamera;window.addEventListener('pagehide',stopCamera);
function resultFile(){return new File([JSON.stringify(snapshot,null,2)+'\n'],`pdinfo-${snapshot.measurement_id}.json`,{type:'application/json'});}
$('save').onclick=()=>{if(!snapshot)return;const url=URL.createObjectURL(resultFile()),a=document.createElement('a');a.href=url;a.download=`pdinfo-${snapshot.measurement_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('share').onclick=async()=>{if(!snapshot)return;try{const file=resultFile();if(!navigator.canShare?.({files:[file]}))throw Error('このブラウザではファイル共有を利用できません。「JSONを保存」を使ってください。');await navigator.share({files:[file],title:'PD測定結果'});}catch(error){if(error.name!=='AbortError')report(error);}};
function consumeFragment(){
  if(!window.location.hash)return;
  try{
    const text=decodeURIComponent(window.location.hash.slice(1));
    if(addPages([text])){
      // Persist before clearing the fragment. No measurement is put in requests.
      if(transfers.persisted)history.replaceState(null,'',location.pathname+location.search);
      if(snapshot)$('result').scrollIntoView({block:'start'});
    }
  }catch(error){report(error);}
}
window.addEventListener('hashchange',consumeFragment);
window.addEventListener('storage',event=>{if(event.key===KPDStore.STORAGE_KEY){transfers.sync();showCurrent();}});
showCurrent();consumeFragment();
