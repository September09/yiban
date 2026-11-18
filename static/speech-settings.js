// Device preferences only: preview never changes system/output gain or stored memories.
var speechPrefs = {data:null, loading:false, saving:false, preview:null, epoch:0};
const speechGenerationFields={seed:'speechSeed',temperature:'speechTemperature',top_k:'speechTopK',top_p:'speechTopP'};
function speechPreviewActive(){return !!speechPrefs.preview;}
function speechSettingsBlocked(){return busy||recording||speaking||enrolling||wakeStarting||wakePhase==='command'||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());}
function speechSettingsControls(){
  if(typeof workspaceActivity==='function')workspaceActivity();
  if(typeof speechPrefs==='undefined')return;
  const locked=speechPrefs.loading||speechPrefs.saving||speechPreviewActive(), unavailable=!speechPrefs.data?.available;
  $('speechVoice').disabled=locked||unavailable;$('speechRate').disabled=locked||unavailable||speechPrefs.data?.supports_rate===false;
  $('speechPreview').disabled=locked||unavailable||speechSettingsBlocked();
  $('speechSave').disabled=locked||unavailable||speechSettingsBlocked();
  $('speechReload').disabled=locked;$('speechPreviewStop').disabled=!speechPreviewActive();
  for(const id of [...Object.values(speechGenerationFields),'speechRestoreGeneration'])if($(id))$(id).disabled=locked||unavailable||speechSettingsBlocked();
}
function speechSelection(){
  const selected={voice:$('speechVoice').value,rate:Number($('speechRate').value)};
  if(speechPrefs.data?.generation_defaults){
    selected.generation={...speechPrefs.data.generation_defaults};
    for(const [key,id] of Object.entries(speechGenerationFields)){
      if(!$(id).value.trim()||!$(id).checkValidity())throw new Error('请填写范围内的声音参数，种子和 top_k 须为整数。');
      selected.generation[key]=Number($(id).value);
    }
  }
  return selected;
}
function displaySpeechSettings(data){
  speechPrefs.data=data;$('speechVoice').replaceChildren();
  const voices=[...data.voices].sort((a,b)=>Number(b.locale.startsWith('zh'))-Number(a.locale.startsWith('zh'))||a.name.localeCompare(b.name));
  for(const v of voices){const option=document.createElement('option');option.value=v.name;option.textContent=`${v.label||(v.name==='Tingting'?'婷婷 · Tingting':v.name)}（${v.locale}）`;$('speechVoice').append(option);}
  $('speechVoice').value=data.current.voice;$('speechRate').value=data.current.rate;$('speechRateValue').textContent=data.current.rate;
  $('speechCurrent').textContent=`已生效：${data.voices.find(v=>v.name===data.current.voice)?.label||data.current.voice} · ${data.supports_rate===false?'自然语速':`语速 ${data.current.rate}`}`;
  $('speechRate').hidden=data.supports_rate===false;
  $('speechRateLabel').hidden=data.supports_rate===false;
  if($('speechGenerationPanel')){
    $('speechGenerationPanel').hidden=!data.generation_defaults;
    if(data.generation_defaults)for(const [key,id] of Object.entries(speechGenerationFields))$(id).value=(data.current.generation||data.generation_defaults)[key];
  }
  $('speechSettingsStatus').textContent=data.available?(data.warning||(data.engine==='Qwen3-TTS'?(data.runtime?.state==='ready'?'声音已准备好 · 可直接试听，后续朗读无需重新加载。':data.runtime?.state==='loading'?'正在后台准备声音 · 准备完成后朗读会更快。':'声音尚未准备好 · 试听时会自动加载。'):'可先试听，再保存。')): '语音环境不可用，请检查本机安装。';
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
  let selection;try{selection=speechSelection();}catch(error){$('speechSettingsStatus').textContent=error.message;return;}
  const epoch=++speechPrefs.epoch;
  const p={id:newTurnId(),controller:new AbortController(),audio:null,url:null};speechPrefs.preview=p;
  p.timeout=setTimeout(()=>{if(speechPrefs.preview===p)stopSpeechPreview(true,'试听超时，已停止。请重试。');},speechPrefs.data.engine==='Qwen3-TTS'?250000:90000);
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
for(const id of Object.values(speechGenerationFields))if($(id))$(id).oninput=()=>{$('speechSettingsStatus').textContent='当前声音参数尚未保存，可先试听。';};
if($('speechRestoreGeneration'))$('speechRestoreGeneration').onclick=()=>{
  if(!speechPrefs.data?.generation_defaults)return;
  for(const [key,id] of Object.entries(speechGenerationFields))$(id).value=speechPrefs.data.generation_defaults[key];
  $('speechSettingsStatus').textContent='已恢复稳定设置的选择，点击「保存声音设置」后生效。';
};
window.addEventListener('pagehide',()=>stopSpeechPreview(false));
speechSettingsControls();
