// Metadata-only self-check. This module never opens a microphone, plays a sound or changes gain.
const audioDevices=(()=>{
  const KEY='yiban-audio-route-v1',names={system:'沿用系统默认',builtin:'电脑内置',respeaker:'ReSpeaker 输入与输出',mixed:'ReSpeaker 拾音＋电脑内置输出'};
  const rules={builtin:/built[ -]?in|内建|内置|macbook|mac.*(?:microphone|speaker)|电脑内置/i,respeaker:/respeaker|xvf3800/i};
  const errors={permission:'麦克风或扬声器权限被拒绝，请检查浏览器站点权限与系统隐私设置。',hidden:'浏览器尚未开放设备名称，无法确认指定设备；本次自检不会申请录音权限。',missing_input:'指定输入未找到，可能未授权、已拔出或设备编号变化；请重新连接并明确选择，不会自动换用其他麦克风。',missing_output:'指定输出未找到，可能未授权、已拔出或设备编号变化；请重新选择，不会自动改用其他扬声器。',ambiguous:'同类设备不止一个，请手动选择具体输入和输出。',sink_unsupported:'此浏览器不支持指定输出设备；请使用支持此功能的 Chrome，或明确保存「沿用系统默认」。',changed:'档位在准备期间发生变化，本次操作已停止，请重试。',mismatch:'浏览器实际设备与指定档位不一致，已停止本次音频操作。',invalid:'音频档位损坏或无法读取，请核对后重新保存；不会自动退回其他设备。',unavailable:'浏览器音频设备接口不可用，请使用本机安全页面及支持的浏览器。',busy:'正在录音、唤醒监听、播放或处理对话，请结束后再切换档位。',constraints:'设备不支持请求的采样或声道设置，请重新选择设备。',device_busy:'无法打开设备，可能被占用、已断开，或被系统权限限制。',autoplay:'浏览器阻止自动播放，请通过「再听一遍」主动播放。',playback:'播放失败，请检查输出设备、系统音量、插线及扬声器供电；尚未证明实际出声。'};
  let applied={profile:'system',input_id:'',output_id:''},version=0,devices=[],permission='unknown',native=null,scanned=false,loading=false,warning='',lastError=null,input=null,output=null,permissionHandle=null;
  function valid(data){return data&&Object.keys(data).length===3&&Object.hasOwn(names,data.profile)&&['input_id','output_id'].every(k=>typeof data[k]==='string'&&data[k].length<=512)&&(data.profile==='system'?!data.input_id&&!data.output_id:!!data.input_id&&!!data.output_id);}
  function read(){try{const raw=localStorage.getItem(KEY);if(raw!==null){const data=JSON.parse(raw);if(!valid(data))throw Error();applied=data;}}catch{applied={profile:'invalid',input_id:'',output_id:''};warning=errors.invalid;}}
  read();
  const supportedSink=()=>typeof HTMLMediaElement!=='undefined'&&typeof HTMLMediaElement.prototype.setSinkId==='function';
  const family=(profile,kind)=>profile==='mixed'?(kind==='audioinput'?'respeaker':'builtin'):profile;
  const list=(profile,kind)=>devices.filter(d=>d.kind===kind&&d.deviceId&&!['default','communications'].includes(d.deviceId)&&rules[family(profile,kind)]?.test(d.label));
  function issue(code){const error=new Error(errors[code]||errors.playback);error.audioCode=code;return error;}
  function codeFor(error,kind){return error.audioCode||({NotAllowedError:kind==='output'?'autoplay':'permission',SecurityError:'permission',NotFoundError:'missing_'+kind,OverconstrainedError:'constraints',NotReadableError:'device_busy',AbortError:'device_busy'}[error.name])||(kind==='input'?'device_busy':'playback');}
  function failed(kind,error){if(!error.audioCode&&lastError?.kind===kind&&['missing_input','missing_output','changed','mismatch'].includes(lastError.code))return lastError.message;const code=codeFor(error,kind);lastError={kind,code,message:errors[code]||errors.playback};render();return lastError.message;}
  function blocked(){return !!((typeof busy!=='undefined'&&busy)||(typeof recording!=='undefined'&&recording)||(typeof speaking!=='undefined'&&speaking)||(typeof enrolling!=='undefined'&&enrolling)||(typeof wakeEnabled!=='undefined'&&wakeEnabled)||(typeof wakeStarting!=='undefined'&&wakeStarting)||(typeof speechPreviewActive==='function'&&speechPreviewActive())||(typeof sharedVoiceActive==='function'&&sharedVoiceActive())||input?.track?.readyState==='live');}
  function controls(){
    const locked=loading||blocked();for(const id of ['audioProfile','audioInput','audioOutput','audioApply','audioReload'])$(id).disabled=locked;
    $('audioRefresh').disabled=loading;$('audioAuthorizeOutput').disabled=locked||typeof navigator.mediaDevices?.selectAudioOutput!=='function';
    if($('audioProfile').value==='system'){$('audioInput').disabled=true;$('audioOutput').disabled=true;}
  }
  async function browserList(){
    if(!navigator.mediaDevices?.enumerateDevices)throw issue('unavailable');
    const found=await navigator.mediaDevices.enumerateDevices();
    devices=found.filter(d=>['audioinput','audiooutput'].includes(d.kind)).map(d=>({kind:d.kind,deviceId:d.deviceId,label:d.label||''}));
    scanned=true;
  }
  async function permissions(){
    try{const result=await navigator.permissions.query({name:'microphone'});permission=result.state;
      if(!permissionHandle){permissionHandle=result;result.onchange=()=>{permission=result.state;if(permission==='denied'&&input?.track?.readyState==='live'){const current=input;current.track.stop();failed('input',issue('permission'));current.onLost?.();}refresh(false);};}}
    catch{permission='unknown';}
  }
  function choose(profile,kind,id){
    if(!Object.hasOwn(names,profile))throw issue('invalid');
    if(profile==='system')return null;
    const options=list(profile,kind),known=devices.some(d=>d.kind===kind&&d.label);
    if(!known)throw issue(permission==='denied'?'permission':'hidden');
    if(id){const exact=options.find(d=>d.deviceId===id);if(!exact)throw issue(kind==='audioinput'?'missing_input':'missing_output');return exact;}
    if(options.length!==1)throw issue(options.length?'ambiguous':kind==='audioinput'?'missing_input':'missing_output');
    return options[0];
  }
  function selectors(){
    const profile=$('audioProfile').value;
    for(const [id,kind,key] of [['audioInput','audioinput','input_id'],['audioOutput','audiooutput','output_id']]){
      const previous=$(id).value;$(id).replaceChildren();
      const candidates=profile==='system'?[]:list(profile,kind);
      const option=document.createElement('option');option.value='';option.textContent=profile==='system'?'随系统默认':candidates.length?'请选择具体设备':'当前浏览器未列出匹配设备';$(id).append(option);
      for(const d of candidates){const o=document.createElement('option');o.value=d.deviceId;o.textContent=d.label;$(id).append(o);}
      const wanted=applied.profile===profile?applied[key]:previous;
      if(candidates.some(d=>d.deviceId===wanted))$(id).value=wanted;
      else if(candidates.length===1)$(id).value=candidates[0].deviceId;
      else $(id).value='';
    }
    controls();
  }
  function append(parent,tag,text){const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;}
  function selectedOutputMetadata(){
    if(!native?.available)return null;
    if(applied.profile==='system')return native.devices.find(d=>d.default_output);
    const browser=devices.find(d=>d.kind==='audiooutput'&&d.deviceId===applied.output_id);
    const matches=browser?native.devices.filter(d=>d.output_channels>0&&(browser.label===d.name||browser.label.startsWith(d.name+' ('))):[];
    return matches.length===1?matches[0]:null;
  }
  function render(){
    $('audioApplied').textContent='已保存档位：'+(names[applied.profile]||'配置异常')+'。仅本浏览器当前站点生效；改变系统默认设备会影响「沿用系统默认」。';
    const nativeIn=native?.devices?.find(d=>d.default_input),nativeOut=native?.devices?.find(d=>d.default_output);
    const actualIn=input?.track?.readyState==='live';
    $('audioActualInput').textContent=actualIn?'当前实际采集：'+(input.label||'名称未提供')+'；设备采样率 '+(input.settings.sampleRate||'未知')+'Hz，'+(input.settings.channelCount||'未知')+' 声道；识别流 16000Hz／单声道。':'当前未采集麦克风，实际输入尚未验证。'+(input?' 最近一次：'+(input.label||'名称未提供')+'。':'');
    $('audioActualOutput').textContent=output?'浏览器'+(output.active?'正在播放':'最近绑定')+'：'+output.label+'；绑定不代表喇叭已出声。':'尚未绑定播放设备；实际输出尚未验证（自检不播放）。';
    $('audioSystemDefaults').textContent='系统默认（'+(native?.checked_at?new Date(native.checked_at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'尚未自检')+'）：输入 '+(nativeIn?.name||'未知')+'；系统默认输出：'+(nativeOut?.name||'未知')+'。这与浏览器指定档位可能不同。';
    const checks=$('audioChecks');checks.replaceChildren();
    append(checks,'p','本次只读自检：不录音、不播放、不申请麦克风权限、不调整音量。');
    append(checks,'p','麦克风站点权限：'+({granted:'浏览器已允许（仍可能受系统权限限制）',denied:'已拒绝',prompt:'尚待用户授权',unknown:'浏览器未提供状态'}[permission]||'未知')+'；指定输出能力：'+(supportedSink()?'浏览器支持，授权及实际设备仍需核对':'不支持，不能保证非默认输出')+'。');
    if(!scanned)append(checks,'p','尚未获取浏览器设备清单。');
    else if(!devices.some(d=>d.label))append(checks,'p',errors.hidden);
    if(permission==='denied')append(checks,'p',errors.permission);
    if(window.isSecureContext===false)append(checks,'p','当前页面不是安全上下文，浏览器可能禁止采集设备；请使用本机回环页面。');
    if(typeof AudioContext==='undefined'||typeof AudioWorkletNode==='undefined')append(checks,'p','浏览器未提供完整的 PCM 录音处理接口，语音录制可能不可用；自检不会创建音频处理器。');
    if(native?.services?.asr===false)append(checks,'p','语音识别未启用或模型文件不齐，请先检查语音配置；设备清单仍可查看。');
    if(native?.services?.tts===false)append(checks,'p','本机朗读未启用，暂不能播放回答或试听；设备自检仍可使用。');
    if(actualIn&&input.track.muted)append(checks,'p','浏览器输入轨道暂时没有数据，请检查设备静音、连接和系统权限。');
    if(output&&(output.active||output.pending)&&(output.element.muted||output.element.volume===0))append(checks,'p','浏览器播放元素处于静音或零音量，可能无法听见；本页未作调整。');
    if(warning)append(checks,'p',warning);
    if(applied.profile!=='system'){
      try{choose(applied.profile,'audioinput',applied.input_id);choose(applied.profile,'audiooutput',applied.output_id);append(checks,'p','指定设备目前在浏览器列表中；录音格式和发声仍待实际使用验证。');}
      catch(error){append(checks,'p',error.message);}
      if(!supportedSink())append(checks,'p',errors.sink_unsupported);
    }
    if(native&&!native.available)append(checks,'p',native.message);
    const meta=selectedOutputMetadata();
    if(meta){append(checks,'p','所选输出的系统读数：'+(typeof meta.muted==='boolean'?(meta.muted?'已静音':'未静音'):'静音状态未知')+'；音量 '+(typeof meta.volume_percent==='number'?Number(meta.volume_percent.toFixed(1))+'%':'未知')+'。未作调整。');
      if(meta.muted===true)append(checks,'p','可能无声原因：所选输出在系统中静音。');
      if(typeof meta.volume_percent==='number'&&meta.volume_percent<=3)append(checks,'p','所选输出音量很低，可能难以听见；本页不会提高音量。');}
    else append(checks,'p','所选输出的静音和音量读数未知；不把未知当成正常。');
    if(input?.settings)append(checks,'p','浏览器报告回声消除：'+(input.settings.echoCancellation===undefined?'未知':input.settings.echoCancellation?'开启（效果未实测）':'未开启')+'。');
    append(checks,'p',applied.profile==='mixed'?'混合通路：ReSpeaker 拾音、电脑放音，硬件回声参考未确认；建议稍后重点验证回声和插话。':applied.profile==='respeaker'?'ReSpeaker 同设备通路；硬件固件、回声消除与插话效果尚未实测，不能仅凭档位判定已生效。':'回声、插话、收音电平与喇叭发声：待语音实测。');
    if(lastError)append(checks,'p','最近'+(lastError.kind==='input'?'输入':'输出')+'异常：'+lastError.message);
    const inventory=$('audioInventory');inventory.replaceChildren();
    if(native?.available){append(inventory,'h4','系统识别的音频设备（与浏览器编号独立）');for(const d of native.devices)append(inventory,'p',d.name+' · 输入 '+d.input_channels+'／输出 '+d.output_channels+' 声道'+(d.sample_rate?' · '+d.sample_rate+'Hz':'')+(d.default_input?' · 默认输入':'')+(d.default_output?' · 默认输出':''));}
    append(inventory,'h4','浏览器可见设备');for(const d of devices)append(inventory,'p',(d.kind==='audioinput'?'输入':'输出')+' · '+(d.label||'名称未开放')+(['default','communications'].includes(d.deviceId)?'（默认别名）':''));
    controls();
  }
  async function refresh(updateSelectors=true){
    if(loading)return;loading=true;controls();$('audioStatus').textContent='正在只读检查设备与权限…';
    try{
      const results=await Promise.allSettled([browserList(),permissions(),fetch('/api/audio/devices').then(async r=>{if(!r.ok)throw Error();return await r.json();})]);
      if(results[0].status==='rejected')warning=errors.unavailable;
      else if(warning===errors.unavailable)warning='';
      native=results[2].status==='fulfilled'?results[2].value:{available:false,devices:[],message:'无法读取系统音频信息；浏览器信息仍可查看。'};
      if(updateSelectors)selectors();$('audioStatus').textContent='只读自检完成。收音、播放、回声消除仍待语音实测。';
    }catch{$('audioStatus').textContent='设备信息暂不可读，请刷新重试。';}
    finally{loading=false;render();}
  }
  async function save(){
    if(blocked()||loading){$('audioStatus').textContent=errors.busy;return;}
    loading=true;controls();
    try{
      await browserList();const profile=$('audioProfile').value;
      const inDev=choose(profile,'audioinput',$('audioInput').value),outDev=choose(profile,'audiooutput',$('audioOutput').value);
      if(profile!=='system'&&!supportedSink())throw issue('sink_unsupported');
      const next={profile,input_id:inDev?.deviceId||'',output_id:outDev?.deviceId||''};
      if(!valid(next))throw issue('invalid');
      if(blocked())throw issue('busy');
      localStorage.setItem(KEY,JSON.stringify(next));applied=next;version++;warning='';lastError=null;
      $('audioStatus').textContent='档位已保存。下次录音／播放按此选择；未录音、未试听，也没有改变系统默认设备或音量。';
    }catch(error){$('audioStatus').textContent='未应用：'+(error.audioCode?error.message:'无法保存或读取设备，请检查浏览器存储和设备权限。');}
    finally{loading=false;render();}
  }
  async function capturePlan(){
    const plan={...applied,version};
    try{
      if(!Object.hasOwn(names,plan.profile))throw issue('invalid');
      if(permission==='denied')throw issue('permission');
      if(plan.profile!=='system')await browserList();
      const selected=choose(plan.profile,'audioinput',plan.input_id);
      if(plan.version!==version)throw issue('changed');
      return {...plan,constraints:{channelCount:1,echoCancellation:true,noiseSuppression:true,...(selected?{deviceId:{exact:selected.deviceId}}:{})}};
    }catch(error){failed('input',error);throw error;}
  }
  function captureStarted(track,plan,onLost){
    const settings=track?.getSettings?.()||{};
    if(plan.version!==version)throw issue('changed');
    if(plan.profile!=='system'&&settings.deviceId!==plan.input_id)throw issue('mismatch');
    input={track,settings,label:track?.label||'',plan,onLost};lastError=null;render();
  }
  function releaseInput(track){if(input?.track===track)render();}
  async function routeOutput(element){
    const plan={...applied,version};
    try{
      if(!Object.hasOwn(names,plan.profile))throw issue('invalid');
      let selected=null;
      if(plan.profile!=='system'){
        if(!supportedSink()||typeof element.setSinkId!=='function')throw issue('sink_unsupported');
        await browserList();selected=choose(plan.profile,'audiooutput',plan.output_id);
        await element.setSinkId(selected.deviceId);
        if(element.sinkId!==selected.deviceId)throw issue('mismatch');
      }else if(element.sinkId){await element.setSinkId('');}
      if(plan.version!==version)throw issue('changed');
      output={element,plan,label:selected?.label||'系统默认',active:false,pending:true};lastError=null;render();
    }catch(error){const wrapped=error.audioCode?error:issue(error.name==='NotAllowedError'?'permission':codeFor(error,'output'));failed('output',wrapped);throw wrapped;}
  }
  function playbackStarted(element){if(output?.element===element){output.active=true;output.pending=false;render();}}
  function releaseOutput(element){if(output?.element===element){output.active=false;output.pending=false;render();}}
  async function deviceChanged(){
    try{
      await browserList();
      if(input?.track?.readyState==='live'&&input.plan.profile!=='system'&&(!devices.some(d=>d.kind==='audioinput'&&d.deviceId===input.plan.input_id)||input.track.getSettings?.().deviceId!==input.plan.input_id)){
        const current=input;current.track.stop();failed('input',issue('missing_input'));current.onLost?.();
      }
      if((output?.active||output?.pending)&&output.plan.profile!=='system'&&!devices.some(d=>d.kind==='audiooutput'&&d.deviceId===output.plan.output_id)){
        const current=output;current.element.pause();current.active=false;current.pending=false;failed('output',issue('missing_output'));current.element.onerror?.();
      }
      $('audioStatus').textContent='设备清单发生变化，已重新核对；指定设备缺失时不会自动换用其他设备。';selectors();render();
    }catch{warning=errors.unavailable;if(input?.track?.readyState==='live'&&input.plan.profile!=='system'){const current=input;current.track.stop();failed('input',issue('unavailable'));current.onLost?.();}if((output?.active||output?.pending)&&output.plan.profile!=='system'){const current=output;current.element.pause();current.active=false;current.pending=false;failed('output',issue('unavailable'));current.element.onerror?.();}render();}
  }
  $('audioProfile').value=Object.hasOwn(names,applied.profile)?applied.profile:'system';
  $('audioProfile').onchange=()=>{selectors();$('audioStatus').textContent='档位尚未保存，请核对具体输入和输出。';};
  $('audioInput').onchange=$('audioOutput').onchange=()=>{$('audioStatus').textContent='选择尚未保存，请点击应用并保存档位。';};
  $('audioRefresh').onclick=()=>refresh();$('audioApply').onclick=save;
  $('audioReload').onclick=()=>{if(blocked())return;warning='';applied={profile:'system',input_id:'',output_id:''};read();version++;$('audioProfile').value=Object.hasOwn(names,applied.profile)?applied.profile:'system';refresh();};
  $('audioAuthorizeOutput').onclick=async()=>{if(blocked())return;try{await navigator.mediaDevices.selectAudioOutput();await refresh();$('audioStatus').textContent='已完成输出设备选择授权，未播放。请核对并保存档位。';}catch(error){failed('output',error.name==='NotAllowedError'?issue('permission'):error);$('audioStatus').textContent='未完成输出授权；未播放声音。';}};
  navigator.mediaDevices?.addEventListener?.('devicechange',deviceChanged);
  window.addEventListener('storage',event=>{if(event.key!==KEY)return;applied={profile:'system',input_id:'',output_id:''};warning='';read();version++;$('audioStatus').textContent='其他页面更新了档位；正在使用的通路保持本轮选择，下次使用新档位。';$('audioProfile').value=Object.hasOwn(names,applied.profile)?applied.profile:'system';selectors();render();});
  render();return {refresh,controls,capturePlan,captureStarted,releaseInput,routeOutput,playbackStarted,releaseOutput,failed,deviceChanged};
})();
