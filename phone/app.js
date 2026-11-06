'use strict';
const $=id=>document.getElementById(id);
let browserStorage=null;try{browserStorage=window.localStorage;}catch{/* In-page scanning still works without persistence. */}
const transfers=new KPDStore.Store(browserStorage);
let snapshot=null,stream=null,scanFrame=null,sample=null,lastError=null;
const languageKey='km003c-pd-interrogator:language';
let language='en';
try{if(browserStorage?.getItem(languageKey)==='ja')language='ja';}catch{/* English remains available. */}
const t=(en,ja)=>language==='ja'?ja:en;
const localError=KPD.localizedError;
const names={spr_pdos:['SPR PDO','SPR PDO'],epr_pdos:['EPR PDO','EPR PDO'],revision:['PD revision','PDリビジョン'],
  source_cap_ext:['Source identification','電源の識別情報'],source_info:['Source PDP','電源のPDP情報'],source_status:['Source status','電源ステータス'],
  pps_status:['PPS status','PPSステータス'],manufacturer:['Manufacturer','メーカー情報'],cable_identity:['Cable identity','ケーブルIdentity'],source_identity:['Source identity','電源Identity']};
const statuses={not_requested:['Not requested','未要求'],pass:['Retrieved','取得成功'],observed:['Observed','受信を記録'],
  unsupported:['Unsupported response','非対応の応答'],no_response:['No response','無応答'],tx_failed:['Send failed','送信失敗'],
  rejected:['Rejected','拒否'],busy:['Busy','待機応答'],reset:['Reset','リセット'],invalid:['Invalid response','取得失敗'],
  partial:['Partial result','一部のみ取得'],unknown:['Unknown','不明'],emulation_enabled:['Recorded with e-marker emulation enabled','e-Markerシミュレーション有効時の記録']};
const measurementNames={km003c:['Power source','電源測定'],km003c_cable:['Cable','ケーブル測定'],km003c_pps:['PPS','PPS測定'],
  ch32h417:['CH32H417','CH32H417'],demo:['DEMO','DEMO']};
const label=(table,key)=>table[key]?t(...table[key]):key;
const staticText=Array.from(document.querySelectorAll('[data-ja]'),node=>[node,node.textContent,node.dataset.ja]);
function applyLanguage(){
  document.documentElement.lang=language;
  document.title=t('KM003C PD Interrogator — QR Import','KM003C PD Interrogator — QR取込');
  $('language').value=language;
  $('pages').placeholder=t('https://…/#KPD1:… or KPD1:…','https://…/#KPD1:… または KPD1:…');
  for(const [node,en,ja] of staticText)node.textContent=t(en,ja);
  showCurrent();report(lastError);
}
function report(error){lastError=error;$('error').textContent=t(error?.message,error?.ja)||error?.message||String(error||'');}
function addPages(texts){
  try{
    transfers.add(texts);sample=null;$('sample').value='';showCurrent();report('');return true;
  }catch(error){report(error);}
  return false;
}
function showCurrent(){
  const result=transfers.current();
  const count=result.total?`${result.received} / ${result.total}`:'0';
  $('counter').textContent=t(`${count} ${result.total===1?'page':'pages'}`,`${count} 枚`);
  $('progress').max=result.total||1;$('progress').value=result.received;
  $('progressText').firstElementChild.textContent=result.snapshot?t('Import complete','すべて読み込みました'):
    result.total?t('Scan the remaining pages','続きのQRを読み込んでください'):t('Ready to import','読込待ち');
  $('nextStep').textContent=result.snapshot?t('Review, save, or share your results.','結果を確認して保存・共有できます。'):
    result.total?t('Open the next QR link from the iPhone Camera app, or keep scanning here.','iPhoneのカメラに戻り、次のQRのリンクを開いてください。この画面のカメラでも続けられます。'):
    t('Open a QR link from the iPhone Camera app to import it automatically.','標準カメラでURL形式のQRを読むと、自動で取り込みます。');
  $('storageNotice').textContent=transfers.persisted?'':t('This browser cannot save your progress. Scan the remaining pages here, then save the result as JSON.',
    'このブラウザでは途中結果を保存できません。この画面のカメラで続きのQRを読み、完了後にJSONを保存してください。');
  const list=transfers.list();$('historyCard').classList.toggle('hidden',!list.length);$('history').replaceChildren();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=t('Choose a saved measurement','保存済みの測定を選ぶ');$('history').append(placeholder);
  for(const item of list){const option=document.createElement('option');option.value=item.key;const title=item.origin?label(measurementNames,item.origin):'';
    const pages=`${item.received}/${item.total}`;
    option.textContent=[title,item.id,t(`${pages} ${item.total===1?'page':'pages'}`,`${pages} 枚`),item.received===item.total?t('Complete','完了'):''].filter(Boolean).join(' · ');
    $('history').append(option);
  }
  $('history').value=sample?'':transfers.state.active||'';
  $('reset').disabled=!!sample || !result.total;
  snapshot=sample?.snapshot||result.snapshot;$('result').classList.toggle('hidden',!snapshot);
  if(snapshot)render();
}
const hexCode=(value,width=2)=>`0x${value.toString(16).toUpperCase().padStart(width,'0')}`;
const onOff=value=>value?t('Yes','あり'):t('No','なし');
const reservedCode=code=>t(`Reserved (${code})`,`予約値（${code}）`);
const codeLabel=(labels,code)=>labels[code]??t(`Reserved or unknown (${code})`,`予約・未知（${code}）`);

function formatPDOs(name, rows){
  const spr=snapshot.fields.spr_pdos.decoded||[];
  const visible=rows.filter(p=>p.kind!=='reserved' && (name!=='epr_pdos' || p.index>7 || spr[p.index-1]?.raw!==p.raw));
  const formatEntry=p=>KPD.formatPDO(p,t).replace(/^(\d+: (?:PPS|EPR AVS)) /,'$1\n  ');
  const basic=visible.map(formatEntry);
  if(visible.length<rows.length)basic.push(t('See details for duplicate and reserved slots','重複・予約スロットは詳細に表示'));
  const revision=snapshot.fields.revision.decoded?.pd_revision;
  const [major,minor]=revision?.split('.').map(Number)||[];
  const detail=rows.map(p=>{
    const lines=[`${formatEntry(p)}\n${t('Raw value','生値')} 0x${p.raw}`];
    if(p.peak_current_code!==undefined)lines.push(`${t('Peak current code','ピーク電流コード')} ${p.peak_current_code}`);
    if(p.reserved && p.reserved!=='00000000')lines.push(`${t('Reserved bits','予約ビット')} 0x${p.reserved}`);
    if(p.capabilities){
      const c=p.capabilities;
      const flags=[[t('Dual-role power','電源ロール切替'),c.dual_role_power],
        ['Unconstrained Power',c.unconstrained_power],[t('USB communications','USB通信'),c.usb_communications_capable],
        [t('Dual-role data','データロール切替'),c.dual_role_data]];
      lines.push(...flags.map(([label,value])=>`${label} ${onOff(value)}`));
      const suspend=c.usb_communications_capable?t('valid after a contract','契約後に有効'):t('undefined without USB communications','USB通信なしのため未定義');
      lines.push(`${t('USB suspend','USBサスペンド')} B28 = ${Number(c.usb_suspend_supported)} (${suspend})`);
      for(const [label,bit,position,minMinor] of [[t('Unchunked extended messages','非分割拡張メッセージ'),c.unchunked_extended_messages_bit,24,0],
        [t('EPR mode capable','EPRモード対応'),c.epr_mode_capable_bit,23,1]]){
        if(major===3 && minor>=minMinor)lines.push(`${label} ${onOff(bit)}`);
        else if(major===2 || (major===3 && minor<minMinor))lines.push(`${t('Reserved bit','予約ビット')} B${position} = ${bit}`);
        else lines.push(`B${position} = ${bit} ${t('(PD revision unknown)','（PDリビジョン未確定）')}`);
      }
    }
    return lines.join('\n');
  }).join('\n\n');
  return {text:basic.join('\n')||t('No PDOs','PDOなし'),detail};
}

function renderPDOText(element,text){
  for(const line of text.split('\n')){
    const row=document.createElement(line?'div':'br');
    if(line.startsWith('  '))row.className='pdo-subline';
    row.textContent=line.trimStart();
    element.append(row);
  }
}

const noFlags=()=>t('No flags set','該当フラグなし');
const temperature=code=>[t('Unsupported','非対応'),t('Normal','正常'),t('Warning','警告'),t('Over temperature','過温度')][code];
function formatStatus(d){
  const input=d.present_input;
  const inputs=[input.external && (input.external_type==='ac'?t('External AC','外部AC'):t('External DC','外部DC')),
    input.internal_battery && t('Internal battery','内部バッテリー'),input.internal_non_battery && t('Internal non-battery power','内部電源（バッテリー以外）')].filter(Boolean);
  const events=[[t('Overcurrent (OCP)','過電流保護（OCP）'),d.events.ocp],[t('Over temperature (OTP)','過温度保護（OTP）'),d.events.otp],[t('Overvoltage (OVP)','過電圧保護（OVP）'),d.events.ovp]];
  const limits=[[t('Cable current','ケーブル電流'),d.power_status.cable],[t('Power to other ports','他ポートへの給電'),d.power_status.other_ports],
    [t('Insufficient external power','外部電源の不足'),d.power_status.external_power],[t('Protection event','保護イベント'),d.power_status.events],[t('Temperature','温度'),d.power_status.temperature]];
  const separator=t(', ','、');
  const active=list=>list.filter(([,set])=>set).map(([label])=>label).join(separator)||noFlags();
  const thermal=temperature(d.temperature_status.code);
  const temp=d.internal_temperature_code===0?t('Unsupported','非対応'):d.internal_temperature_code===1?t('Below 2 °C','2 °C未満'):`${d.internal_temperature_c} °C`;
  const batteries=d.present_battery_input.active;
  const battery=batteries===null?t('Not applicable','対象外'):batteries.map(i=>`${i<4?t('Fixed','固定'):t('Hot-swappable','交換式')} #${i}`).join(separator)||noFlags();
  const detail=[`${t('Internal temperature','内部温度')} ${temp} (${hexCode(d.internal_temperature_code)})`,
    `${t('Battery input','バッテリー入力')} ${battery} (${hexCode(d.present_battery_input.raw)})`,
    `${t('PPS operating mode bit','PPS動作モードビット')} ${Number(d.events.pps_current_limit)} ${t('(PPS only. 1 = current limit, 0 = constant voltage)','（PPS動作時のみ有効。1 は電流制限、0 は定電圧）')}`];
  const state=d.power_state_change;
  if(state){
    const states=[t('Unsupported','非対応'),'S0','Modern Standby','S3','S4','S5','G3'];
    detail.push(`${t('Power state','電源状態')} ${states[state.state_code]??reservedCode(state.state_code)}`);
    const indicators=t(['Off','On','Blinking','Breathing'],['消灯','点灯','点滅','明滅']);
    let indicator=indicators[state.indicator_code]??reservedCode(state.indicator_code);
    if(state.indicator_code<indicators.length && (state.state_code===0 || state.state_code>=states.length)){
      indicator=t(`Code ${state.indicator_code} (state unknown)`,`コード ${state.indicator_code}（状態を判定できません）`);
    }
    detail.push(`${t('LED indicator','LED指示')} ${indicator}`);
  }else detail.push(t('Power state byte absent','電源状態のバイトなし'));
  for(const [en,ja,value] of [['Input','入力',input],['Battery','バッテリー',d.present_battery_input],['Event','イベント',d.events],
    ['Temperature status','温度状態',d.temperature_status],['Power limit','電力制限',d.power_status],['Power state','電源状態',state]]){
    if(value?.reserved)detail.push(`${t(`${en} reserved bits`,`${ja}の予約ビット`)} ${hexCode(value.reserved)}`);
  }
  if(d.trailing_hex)detail.push(`${t('Uninterpreted trailing bytes','未解釈の末尾')} 0x${d.trailing_hex}`);
  return {text:[`${t('Input','入力')} ${inputs.join(separator)||noFlags()}`,`${t('Temperature status','温度状態')} ${thermal}`,
    `${t('Events','イベント')} ${active(events)}`,`${t('Output limits','出力制限')} ${active(limits)}`].join('\n'),detail:detail.join('\n'),warning:d.temperature_status.code>=2 || [...events,...limits].some(([,set])=>set)};
}

const ufpTypes={none:['Undefined','未定義'],hub:['PDUSB Hub','PDUSB Hub'],peripheral:['PDUSB Peripheral','PDUSB Peripheral'],psd:['Power Supply Device','Power Supply Device'],legacy_ama:['Legacy AMA','旧AMA']};
const dfpTypes={none:ufpTypes.none,hub:ufpTypes.hub,host:['PDUSB Host','PDUSB Host'],power_brick:['Power Brick','Power Brick']};
function formatIdentity(d){
  const identity=d.identity;
  if(!identity)return {text:t('Could not decode a Discover Identity ACK','Discover Identity ACKとして解釈できません'),detail:formatVDOs(d.vdos,[])};
  const id=identity;
  const ufp=label(ufpTypes,id.ufp_type)??codeLabel([],id.ufp_type_code);
  const dfp=id.dfp_type_code===null?t('Layout unknown or not applicable','レイアウト未確定・対象外'):label(dfpTypes,id.dfp_type)??codeLabel([],id.dfp_type_code);
  const text=[`VID ${id.vid} / PID ${id.pid}`];
  if(id.dfp_type && id.dfp_type!=='none')text.push(`DFP ${dfp}`);
  if(id.ufp_type && id.ufp_type!=='none')text.push(`UFP ${ufp}`);
  const vdm=id.vdm;
  const detail=[`Discover Identity ACK / SVID 0x${vdm.svid}`,`SVDM ${vdm.version??`${t('Unknown','未知')} (${vdm.major_code}, ${vdm.minor_code})`}`,
    `XID 0x${id.xid}`,`${t('Product revision (BCD)','製品リビジョン（BCD）')} 0x${id.bcd_device}${/^[0-9]{4}$/.test(id.bcd_device)?'':t(' (uninterpreted)','（未解釈）')}`,
    `UFP ${ufp} / DFP ${dfp}`,`USB Host ${onOff(id.usb_host_capable)} / USB Device ${onOff(id.usb_device_capable)}`,
    `Modal Operation ${onOff(id.modal_operation)}`];
  if(id.connector_type_code!==null)detail.push(`${t('Connector','端子')} ${codeLabel([null,null,t('USB Type-C receptacle','USB Type-C レセプタクル'),t('USB Type-C plug','USB Type-C プラグ')],id.connector_type_code)}`);
  if(id.id_header_reserved && id.id_header_reserved!=='00000000')detail.push(`${t('ID Header reserved bits','IDヘッダー予約ビット')} 0x${id.id_header_reserved}`);
  detail.push(formatVDOs(d.vdos.slice(0,4),[t('VDM Header','VDMヘッダー'),t('ID Header','IDヘッダー'),'Cert Stat',t('Product VDO','製品VDO')]));
  const setBits=(code,labels)=>labels.filter((_,i)=>code&(1<<i)).join(' / ')||noFlags();
  for(const v of id.product_type_vdos){
    const label={ufp:'UFP VDO',dfp:'DFP VDO',pad:t('Pad VDO','パッドVDO'),unknown:t('Uninterpreted VDO','未解釈VDO')}[v.kind];
    detail.push(`${v.index}. ${label} 0x${v.raw}`);
    if(v.version_code!==undefined)detail.push(`${t('VDO version','VDO版')} ${v.version??t(`Uninterpreted (code ${v.version_code})`,`未解釈（コード ${v.version_code}）`)}`);
    if(v.host_capability_code!==undefined)detail.push(`${t('Host capabilities','ホスト能力')} ${setBits(v.host_capability_code,['USB 2.0','USB 3.2','USB4'])} (${hexCode(v.host_capability_code)})`,
      `${t('Port number','ポート番号')} ${v.port_number}`);
    if(v.device_capability_code!==undefined)detail.push(`${t('Device capabilities','デバイス能力')} ${setBits(v.device_capability_code,['USB 2.0',t('USB 2.0 Billboard only','USB 2.0 Billboard専用'),'USB 3.2','USB4'])} (${hexCode(v.device_capability_code)})`,
      `${t('Alternate mode bits','代替モードビット')} ${hexCode(v.alternate_mode_code)} / ${t('Speed code','速度コード')} ${v.speed_code}`);
    if(v.reserved && v.reserved!=='00000000')detail.push(`${t('Reserved or legacy bits','予約・旧定義ビット')} 0x${v.reserved}`);
  }
  return {text:text.join('\n'),detail:detail.join('\n')};
}

function formatVDOs(vdos,labels){
  return vdos.map((vdo,i)=>`${labels[i]||`VDO ${i+1}`} 0x${vdo}`).join('\n');
}

function formatCableDetails(d){
  const c=d.cable;
  if(!c)return formatVDOs(d.vdos,[]);
  const labels=[t('ID Header','IDヘッダー'),'Cert Stat',t('Product VDO','製品VDO'),t('Cable VDO','ケーブルVDO')];
  if(c.layout!=='pd3_assumed')labels.unshift(t('VDM Header','VDMヘッダー'));
  const raw=formatVDOs(d.vdos,labels);
  const legacy=c.layout==='pd2',passive=c.kind==='passive';
  const connectors=[legacy?'USB Type-A':null,legacy?'USB Type-B':null,'USB Type-C',t('Captive','キャプティブ')];
  const latencies=[null,t('Below 10 ns','10 ns未満'),'10–20 ns','20–30 ns','30–40 ns','40–50 ns','50–60 ns','60–70 ns'];
  if(!legacy && !passive && c.vdo_version_code===3)latencies.push('1000 ns','2000 ns','3000 ns');
  else if(legacy || passive)latencies.push(t('Above 70 ns','70 ns超'));
  const terminations=passive?t(['Not required','Required'],['不要','必要']):[null,null,t('One active end, VCONN required','片端アクティブ・VCONN必要'),t('Two active ends, VCONN required','両端アクティブ・VCONN必要')];
  const layout=legacy?'PD 2.0':c.layout==='pd3_assumed'?t('PD 3.x (assumed)','PD 3.x（推定）'):'PD 3.x';
  const detail=[`${t('VDO layout','VDOレイアウト')} ${layout}`,
    `${t('HW version','HW版')} ${hexCode(c.hw_version,1)} / ${t('FW version','FW版')} ${hexCode(c.fw_version,1)}`,
    `${t('Far-end connector','相手側の端子')} ${codeLabel(connectors,c.connector_type_code)}`,
    `${t('Latency','レイテンシ')} ${codeLabel(latencies,c.latency_code)}`,
    `${t('VCONN termination','VCONN終端')} ${codeLabel(terminations,c.termination_code)}`,`${t('USB speed code','USB速度コード')} ${c.speed_code}`];
  if(c.vdo_version_code!==null){
    const version=passive?'1.0':c.vdo_version_code===3?'1.3':null;
    const code=`${t('code','コード')} ${c.vdo_version_code}`;
    detail.push(`${t('Cable VDO version','Cable VDO版')} ${version?`${version} (${code})`:code}`);
  }
  if(c.voltage_code!==null)detail.push(`${t('VBUS voltage code','VBUS電圧コード')} ${c.voltage_code}`);
  detail.push(`${t('VBUS current code','VBUS電流コード')} ${c.current_code}`);
  if(c.sbu_supported!==undefined)detail.push(`SBU ${c.sbu_supported?(c.sbu_active?t('Active','アクティブ'):t('Passive','パッシブ')):t('Unsupported','非対応')}`,
    `${t('SOP″ controller','SOP″コントローラー')} ${onOff(c.sop_double_prime_present)}`);
  return [...detail,raw].join('\n');
}

function render(){
  $('result').classList.remove('hidden');$('badges').replaceChildren();$('rows').replaceChildren();
  const cable=snapshot.origin==='km003c_cable';
  const pps=snapshot.origin==='km003c_pps';
  const badges=[label(measurementNames,snapshot.origin),`ID ${snapshot.measurement_id}`];
  if(sample && snapshot.origin!=='demo')badges.unshift('DEMO');
  for(const text of badges){const badge=document.createElement('span');badge.className='badge';badge.textContent=text;$('badges').append(badge);}
  const warning=sample || snapshot.origin==='demo'?t('This demo uses synthetic data.','デモ用の架空データです。実際の測定結果ではありません。'):
    ['failed','interrupted','unknown'].includes(snapshot.capture_status)?t(`Capture ${snapshot.capture_status}. Some information may be missing.`,
      `取得状態 ${label({failed:['Failed','失敗'],interrupted:['Interrupted','中断'],unknown:statuses.unknown},snapshot.capture_status)}。一部の情報が取得できていない可能性があります。`):'';
  $('notice').textContent=warning;$('notice').classList.toggle('hidden',!warning);
  for(const [name,field]of Object.entries(snapshot.fields)){
    if((cable && name!=='cable_identity') || (pps && name!=='pps_status'))continue;
    const tr=document.createElement('tr'),th=document.createElement('th'),td=document.createElement('td');
    th.scope='row';th.textContent=label(names,name);
    const d=field.decoded,status=label(statuses,field.status);
    let text=status,detail='',isWarning=false;
    const isPDO=Array.isArray(d);
    if(!field.raw && field.status==='pass')text=t('Response received (this QR has no raw data)','応答成功（このQRに生データなし）');
    if(!field.raw && field.status==='partial')text=t('Partial result (raw data incomplete)','一部のみ取得（完全な生データなし）');
    if(d){
      if(isPDO)({text,detail}=formatPDOs(name,d));
      else if(name==='revision'){
        text=`Rev ${d.pd_revision} / Spec Ver ${d.pd_spec_version}`;
        if(d.reserved)detail=`${t('Reserved bits','予約ビット')} ${hexCode(d.reserved,4)}`;
      }
      else if(name==='source_info'){
        text=`${t('Maximum','最大')} ${d.port_maximum_pdp_w} W\n${t('Present','現在')} ${d.port_present_pdp_w} W / ${t('Reported','報告')} ${d.port_reported_pdp_w} W`;
        const port=d.port_type_name==='guaranteed'?t('Guaranteed','保証型'):t('Managed','電力管理型');
        detail=`${t('PDP values reported by the source','電源からのPDP報告値')}\n${t('Port type','ポート種別')} ${port} (${d.port_type})\n${t('Reserved bits','予約ビット')} ${hexCode(d.reserved)}`;
      }
      else if(name==='source_cap_ext'){
        text=`VID ${d.vid} / PID ${d.pid}\nSPR ${d.spr_pdp_w} W${d.epr_pdp_w===null?'':` / EPR ${d.epr_pdp_w} W`}`;
        detail=`XID 0x${d.xid}\n${t('FW version','FW版')} ${hexCode(d.fw_version)} / ${t('HW version','HW版')} ${hexCode(d.hw_version)}`;
      }
      else if(name==='manufacturer')text=`${d.name||t('Name not supplied','名称未提供')}\nVID ${d.vid} / PID ${d.pid}`;
      else if(name==='source_status')({text,detail,warning:isWarning}=formatStatus(d));
      else if(name==='source_identity')({text,detail}=formatIdentity(d));
      else if(name==='pps_status'){
        const value=(number,unit)=>number===null?t('Not reported','報告なし'):`${number/1000} ${unit}`;
        const lines=[];
        if(d.requested_mv!==undefined)lines.push(`${t('Requested','要求値')} ${value(d.requested_mv,'V')} / ${value(d.requested_ma,'A')}`);
        if(d.reported_mv!==undefined){
          lines.push(`${t('Source reported','電源の応答')} ${value(d.reported_mv,'V')} / ${value(d.reported_ma,'A')}`);
          lines.push(d.current_limit?t('Current limiting','電流制限中'):t('Constant voltage','定電圧動作'));
          if(d.temperature_code)lines.push(`${t('Temperature status','温度状態')} ${temperature(d.temperature_code)}`);
        }
        text=lines.join('\n');
      }
      else if(name==='cable_identity'){
        if(d.svids)text=`${t('Identity unavailable','Identity未取得')}\nSVID ${d.svids.map(id=>`0x${id}`).join(' / ')}`;
        else{
          const count=d.vdos.length;
          text=d.cable?KPD.formatCable(d.cable,t):t(`${count} ${count===1?'VDO':'VDOs'} saved (cable specifications unknown)`,`${count} VDOを保存（ケーブルの仕様は不明）`);
          detail=formatCableDetails(d);
        }
      }
      text=field.status==='emulation_enabled'?`${status}\n${text}`:`${text}\n${status}`;
    }else if(field.raw!==undefined){
      if(name==='source_status')text+='\n'+t('Status payload too short','Statusの長さ不足');
      else if(name==='pps_status')text+='\n'+t('Could not decode PPS status','PPSステータスの形式を解釈できません');
    }
    const overview=document.createElement('div');overview.className=isWarning?'field-text field-warning':'field-text';
    if(isPDO)renderPDOText(overview,text);
    else overview.textContent=text;
    td.append(overview);
    if(detail || field.raw){
      const disclosure=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('div');
      summary.textContent=t('Details and raw data','詳細と生データ');body.className='field-text field-detail';
      const detailText=[detail,field.raw?`${t('Raw data','生データ')} 0x${field.raw}`:''].filter(Boolean).join('\n\n');
      if(isPDO)renderPDOText(body,detailText);
      else body.textContent=detailText;
      disclosure.append(summary,body);td.append(disclosure);
    }
    tr.append(th,td);$('rows').append(tr);
  }
  $('raw').textContent=serializeResult();
}
$('add').onclick=()=>{addPages($('pages').value.split(/\r?\n/));};
$('reset').onclick=()=>{transfers.clear();$('pages').value='';showCurrent();report('');stopCamera();};
$('history').onchange=()=>{if(!$('history').value)return;try{transfers.select($('history').value);sample=null;$('sample').value='';showCurrent();report('');}catch(error){report(error);}};
$('language').onchange=()=>{
  language=$('language').value==='ja'?'ja':'en';
  try{browserStorage?.setItem(languageKey,language);}catch{/* Still switch for this page. */}
  applyLanguage();
};
$('sample').onchange=()=>{
  const key=$('sample').value;
  const pages=Object.hasOwn(KPDSamples,key)?KPDSamples[key]:null;
  sample=pages?{type:key,snapshot:KPD.assemble(pages).snapshot}:null;
  showCurrent();report('');
  if(sample)$('result').scrollIntoView({block:'start'});
};
$('files').onchange=async event=>{
  try{
    for(const file of event.target.files){
      if(file.size>20*1024*1024)throw localError('Choose a file no larger than 20 MB.','ファイルは20 MB以下にしてください');
      if(file.type.startsWith('image/')){
        const url=URL.createObjectURL(file),image=new Image();
        try{image.src=url;await image.decode();const scale=Math.min(1,2048/Math.max(image.width,image.height));const canvas=document.createElement('canvas');canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);const context=canvas.getContext('2d',{willReadFrequently:true});context.fillStyle='white';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);const data=context.getImageData(0,0,canvas.width,canvas.height);const code=jsQR(data.data,data.width,data.height);if(!code)throw localError(`No QR code found in ${file.name}`,`${file.name}: QRを見つけられませんでした`);addPages([code.data]);}
        finally{URL.revokeObjectURL(url);}
      }else{if(file.size>10000)throw localError('The QR text file is too large.','QRテキストファイルが大きすぎます');addPages((await file.text()).split(/\r?\n/));}
    }
  }catch(error){report(error);}finally{event.target.value='';}
};
function stopCamera(){if(scanFrame)cancelAnimationFrame(scanFrame);scanFrame=null;if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;$('video').srcObject=null;$('video').classList.add('hidden');$('stopCamera').classList.add('hidden');}
$('camera').onclick=async()=>{
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw localError('The camera is unavailable on this connection. Choose a QR image or paste the QR text.','この接続ではカメラを使えません。QR画像を選ぶか、QRテキストを貼り付けてください。');
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
function serializeResult(){
  let result=snapshot;
  if(sample)result={...snapshot,demo:true,synthetic:true,sample_type:sample.type};
  else if(snapshot.origin==='demo')result={...snapshot,synthetic:true};
  return JSON.stringify(result,null,2);
}
function resultFile(){return new File([serializeResult()+'\n'],`pdinfo-${snapshot.measurement_id}.json`,{type:'application/json'});}
$('save').onclick=()=>{if(!snapshot)return;const url=URL.createObjectURL(resultFile()),a=document.createElement('a');a.href=url;a.download=`pdinfo-${snapshot.measurement_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('share').onclick=async()=>{if(!snapshot)return;try{const file=resultFile();if(!navigator.canShare?.({files:[file]}))throw localError('This browser cannot share files. Use “Save JSON”.','このブラウザではファイル共有を利用できません。「JSONを保存」を使ってください。');await navigator.share({files:[file],title:t('PD measurement results','PD測定結果')});}catch(error){if(error.name!=='AbortError')report(error);}};
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
applyLanguage();consumeFragment();
