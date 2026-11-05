'use strict';
const $=id=>document.getElementById(id), accepted=new Map();
let snapshot=null,stream=null,scanFrame=null;
const names={spr_pdos:'SPR PDO',epr_pdos:'EPR PDO',revision:'PDリビジョン',source_cap_ext:'電源の識別情報',source_info:'電源のPDP情報',source_status:'電源ステータス',pps_status:'PPSステータス',manufacturer:'メーカー情報',cable_identity:'ケーブルIdentity',source_identity:'電源Identity'};
const statuses={not_requested:'未要求',pass:'取得成功',observed:'受信を記録',unsupported:'非対応の応答',no_response:'無応答',tx_failed:'送信失敗',rejected:'拒否',busy:'待機応答',reset:'リセット',invalid:'取得失敗',partial:'一部のみ取得',unknown:'不明'};
function report(error){$('error').textContent=error?.message||String(error||'');}
function addPages(texts){
  try{
    const next=new Map(accepted);
    for(const text of texts.filter(t=>t.trim())){const page=KPD.parsePage(text);next.set(page.text,page.text);}
    const result=KPD.assemble([...next.values()]);
    accepted.clear();for(const [k,v]of next)accepted.set(k,v);
    $('counter').textContent=`${result.received} / ${result.total} 枚`;
    $('progress').max=result.total||1;$('progress').value=result.received;
    $('progressText').firstElementChild.textContent=result.snapshot?'すべて読み込みました':'続きのQRを読み込んでください';
    snapshot=result.snapshot;report('');
    if(snapshot)render();
  }catch(error){report(error);}
}
function render(){
  $('result').classList.remove('hidden');$('badges').replaceChildren();$('rows').replaceChildren();
  for(const text of [snapshot.origin.toUpperCase(),`ID ${snapshot.measurement_id}`]){const badge=document.createElement('span');badge.className='badge';badge.textContent=text;$('badges').append(badge);}
  const warning=snapshot.origin==='demo'?'デモ用の架空データです。実際の測定結果ではありません。':['failed','interrupted','unknown'].includes(snapshot.capture_status)?`取得状態: ${snapshot.capture_status}。一部の情報が取得できていない可能性があります。`:'';
  $('notice').textContent=warning;$('notice').classList.toggle('hidden',!warning);
  for(const [name,field]of Object.entries(snapshot.fields)){
    const tr=document.createElement('tr'),th=document.createElement('th'),td=document.createElement('td');
    th.scope='row';th.textContent=names[name];
    const d=field.decoded;let text=statuses[field.status]||field.status;
    if(!field.raw && field.status==='pass')text='応答成功（このQRに生データなし）';
    if(!field.raw && field.status==='partial')text='一部のみ取得（完全な生データなし）';
    if(d){
      if(Array.isArray(d))text=d.map(p=>p.kind==='fixed'?`${p.index}: ${p.voltage_mv/1000} V / ${p.current_ma/1000} A`:p.kind==='pps'?`${p.index}: PPS ${p.min_mv/1000}–${p.max_mv/1000} V / ${p.current_ma/1000} A`:`${p.index}: ${p.kind} ${p.raw}`).join('\n');
      else if(name==='revision')text=`Rev ${d.pd_revision} / Spec Ver ${d.pd_spec_version}`;
      else if(name==='source_info')text=`報告 ${d.port_reported_pdp_w} W / 現在 ${d.port_present_pdp_w} W / 最大 ${d.port_maximum_pdp_w} W`;
      else if(name==='source_cap_ext')text=`VID ${d.vid} / PID ${d.pid}\nSPR ${d.spr_pdp_w} W${d.epr_pdp_w===null?'':` / EPR ${d.epr_pdp_w} W`}`;
      else if(name==='manufacturer')text=d.name||`名称は空欄 / VID ${d.vid} / PID ${d.pid}`;
      else text=`${d.vdos.length} VDOを保存`;
      text+=`\n${statuses[field.status]}`;
    }else if(field.raw)text+=`\n${field.raw}`;
    td.textContent=text;td.style.whiteSpace='pre-line';tr.append(th,td);$('rows').append(tr);
  }
  $('raw').textContent=JSON.stringify(snapshot,null,2);
}
$('add').onclick=()=>{addPages($('pages').value.split(/\r?\n/));};
$('reset').onclick=()=>{accepted.clear();snapshot=null;$('pages').value='';$('result').classList.add('hidden');$('counter').textContent='0 枚';$('progress').value=0;$('progressText').firstElementChild.textContent='読込待ち';report('');stopCamera();};
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
