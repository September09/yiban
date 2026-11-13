// Device preferences only: preview never changes system/output gain or stored memories.
var speechPrefs = {data:null, loading:false, saving:false, preview:null, epoch:0};
function speechPreviewActive(){return !!speechPrefs.preview;}
function speechSettingsBlocked(){return busy||recording||speaking||enrolling||wakeStarting||wakePhase==='command'||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());}
function speechSettingsControls(){
  if(typeof workspaceActivity==='function')workspaceActivity();
  if(typeof speechPrefs==='undefined')return;
  const locked=speechPrefs.loading||speechPrefs.saving||speechPreviewActive(), unavailable=!speechPrefs.data?.available;
  $('speechVoice').disabled=locked||unavailable;$('speechRate').disabled=locked||unavailable;
  $('speechPreview').disabled=locked||unavailable||speechSettingsBlocked();
  $('speechSave').disabled=locked||unavailable||speechSettingsBlocked();
  $('speechReload').disabled=locked;$('speechPreviewStop').disabled=!speechPreviewActive();
}
function speechSelection(){return {voice:$('speechVoice').value,rate:Number($('speechRate').value)};}
function displaySpeechSettings(data){
  speechPrefs.data=data;$('speechVoice').replaceChildren();
  const voices=[...data.voices].sort((a,b)=>Number(b.locale.startsWith('zh'))-Number(a.locale.startsWith('zh'))||a.name.localeCompare(b.name));
  for(const v of voices){const option=document.createElement('option');option.value=v.name;option.textContent=`${v.name==='Tingting'?'婷婷 · Tingting':v.name}（${v.locale}）`;$('speechVoice').append(option);}
  $('speechVoice').value=data.current.voice;$('speechRate').value=data.current.rate;$('speechRateValue').textContent=data.current.rate;
  $('speechCurrent').textContent=`已生效：${data.current.voice} · 语速 ${data.current.rate}`;
  $('speechSettingsStatus').textContent=data.available?(data.warning||'可先试听，再保存。'): '当前平台未启用 Mac 朗读，暂不支持调整音色。';
}
async function loadSpeechSettings(){
  if(speechPrefs.loading||speechPrefs.saving||speechPreviewActive())return;
  speechPrefs.loading=true;speechSettingsControls();
  try{const res=await fetch('/api/speech/settings');const data=await res.json();if(!res.ok)throw new Error(data.error);displaySpeechSettings(data);}
  catch(error){$('speechSettingsStatus').textContent='读取失败：'+error.message;}
  finally{speechPrefs.loading=false;speechSettingsControls();}
}
function stopSpeechPreview(resume=true,message='已停止试听，未更改保存的设置。'){
  const p=speechPrefs.preview;if(!p)return;
  speechPrefs.epoch++;speechPrefs.preview=null;p.controller.abort();clearTimeout(p.timeout);
  if(p.audio){p.audio.onended=null;p.audio.onerror=null;if(typeof audioDevices!=='undefined')audioDevices.releaseOutput(p.audio);p.audio.pause();}
  if(p.url)URL.revokeObjectURL(p.url);
  api('/api/turn/cancel',{turn_id:p.id}).catch(()=>{});
  $('speechSettingsStatus').textContent=message;speechSettingsControls();
  if(resume)returnToWake();
}
async function previewSpeech(){
  if(speechSettingsBlocked()||speechPreviewActive()||!speechPrefs.data?.available||speechPrefs.loading||speechPrefs.saving)return;
  const selection=speechSelection(),epoch=++speechPrefs.epoch;
  const p={id:newTurnId(),controller:new AbortController(),audio:null,url:null};speechPrefs.preview=p;
  p.timeout=setTimeout(()=>{if(speechPrefs.preview===p)stopSpeechPreview(true,'试听超时，已停止。请重试。');},90000);
  speechSettingsControls();$('speechSettingsStatus').textContent='准备试听…沿用当前系统音量。';
  try{
    await pauseWake();if(epoch!==speechPrefs.epoch)return;
    const response=await api('/api/speech/preview',{...selection,turn_id:p.id},'application/json',p.controller.signal);
    const blob=await response.blob();if(epoch!==speechPrefs.epoch)return;
    p.url=URL.createObjectURL(blob);p.audio=new Audio(p.url);
    p.audio.onended=()=>{if(epoch===speechPrefs.epoch)stopSpeechPreview(true,'试听结束。满意后请点击「保存声音设置」。');};
    p.audio.onerror=()=>{if(epoch===speechPrefs.epoch)stopSpeechPreview(true,'试听播放失败，请检查当前输出设备。');};
    if(typeof audioDevices!=='undefined')await audioDevices.routeOutput(p.audio);if(epoch!==speechPrefs.epoch)return;
    await p.audio.play();if(epoch===speechPrefs.epoch&&typeof audioDevices!=='undefined')audioDevices.playbackStarted(p.audio);if(epoch===speechPrefs.epoch)$('speechSettingsStatus').textContent='正在试听…没有提高系统音量。';
  }catch(error){if(epoch===speechPrefs.epoch&&p.audio&&typeof audioDevices!=='undefined')audioDevices.failed('output',error);if(epoch===speechPrefs.epoch)stopSpeechPreview(true,'试听失败：'+error.message);}
}
async function saveSpeechSettings(){
  if(speechSettingsBlocked()||speechPreviewActive()||speechPrefs.loading||speechPrefs.saving||!speechPrefs.data?.available)return;
  speechPrefs.saving=true;speechSettingsControls();
  try{const result=await(await api('/api/speech/settings/save',{...speechSelection(),revision:speechPrefs.data.revision})).json();displaySpeechSettings(result);$('speechSettingsStatus').textContent='已保存。下一次朗读生效，重启后仍保留。';}
  catch(error){$('speechSettingsStatus').textContent='未保存：'+error.message;}
  finally{speechPrefs.saving=false;speechSettingsControls();}
}
$('speechSettingsPanel').ontoggle=()=>{if($('speechSettingsPanel').open&&!speechPrefs.data)loadSpeechSettings();};
$('speechReload').onclick=loadSpeechSettings;$('speechPreview').onclick=previewSpeech;$('speechPreviewStop').onclick=()=>stopSpeechPreview();$('speechSave').onclick=saveSpeechSettings;
$('speechRate').oninput=()=>{$('speechRateValue').textContent=$('speechRate').value;$('speechSettingsStatus').textContent='当前选择尚未保存，可先试听。';};
$('speechVoice').onchange=()=>{$('speechSettingsStatus').textContent='当前选择尚未保存，可先试听。';};
window.addEventListener('pagehide',()=>stopSpeechPreview(false));
speechSettingsControls();
