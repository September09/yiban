const $ = id => document.getElementById(id);
function readChatHistory(identity=''){
  try{
    const key=identity?'xiaoban-history-'+identity:'xiaoban-history';
    const history=JSON.parse(localStorage.getItem(key)||'[]');
    if(!Array.isArray(history))return [];
    return history.filter(item=>item&&['user','assistant'].includes(item.role)&&typeof item.content==='string')
      .slice(-30).map(item=>({role:item.role,content:item.content}));
  }catch{return [];}
}
let messages=readChatHistory();
let ready=false, asrReady=false, wakeReady=false, visionReady=false, ttsReady=true, busy=false, recording=false, speaking=false;
let wakeEnabled=false, wakePhase='', wakeStarting=false, wakeListener=null;
let audio=null, audioUrl=null, speechVersion=0, manual=null, frames=[], timer, tick;
let activeProfile='', speakerReady=false, turnVersion=0, turnId=null, chatAbort=null, ttsAbort=null, activeRow=null, enrolling=false;
let cameraStream=null, cameraStarting=false, cameraVersion=0, motion=null, motionTimer;
const welcome='我是小伴，现在可以听你叫我，也可以看你拍的照片。\n开启语音唤醒后，说「你好小伴」，看到我回应后再提问。你也可以试试说「请点点头」。';

function state(text,mode=''){ $('state').textContent=text; document.body.className=mode; }
function notice(text=''){ $('notice').textContent=text; }
function controls(){
  if(typeof workspaceActivity==='function')workspaceActivity();
  if(typeof speechSettingsControls==='function')speechSettingsControls();
  $('send').disabled=enrolling||busy||!ready||recording||wakePhase==='command';
  $('mic').disabled=enrolling||busy||!ready||!asrReady||wakeStarting||wakePhase==='command';
  $('wake').disabled=enrolling||(!wakeEnabled&&(busy||recording||!ready||!wakeReady))||wakeStarting;
  $('wake').textContent=wakeEnabled?'关闭语音唤醒':'开启语音唤醒';$('wake').setAttribute('aria-pressed',String(wakeEnabled));
  $('clear').disabled=enrolling||busy||recording||wakePhase==='command';$('input').disabled=recording||wakePhase==='command';
  $('capture').disabled=enrolling||!cameraStream||!visionReady||busy||recording||wakePhase==='command';
  $('cameraToggle').disabled=cameraStarting;
  $('profileSelect').disabled=busy||recording||enrolling||wakePhase==='command';
  $('speakerAuto').disabled=!speakerReady||enrolling||busy||recording;
  $('bargeIn').disabled=enrolling;
  $('speakerRetry').disabled=$('mic').disabled||!$('speakerAuto').checked||
    (typeof sharedVoiceActive==='function'&&sharedVoiceActive());
  if(typeof personalControls==='function')personalControls();
  if(typeof sharedVoiceControls==='function')sharedVoiceControls();
  document.querySelectorAll('[data-action]').forEach(b=>b.disabled=enrolling||busy||recording||wakePhase==='command');
}
function idle(){if(busy||recording||speaking||wakePhase==='command')return;state(wakeEnabled?'轻声叫我「你好小伴」':'我在这里，随时可以聊',wakeEnabled?'waiting':'');}
function persist(){try{localStorage.setItem(activeProfile?'xiaoban-history-'+activeProfile:'xiaoban-history',JSON.stringify(messages.slice(-30)));}catch{notice('浏览器存储不可用，对话仅在当前页面保留。');}}
function scroll(){$('messages').scrollTop=$('messages').scrollHeight;}
function addMessage(role,text,image){
  const item=document.createElement('div');item.className=`message ${role}`;
  const name=document.createElement('div');name.className='message-name';name.textContent=role==='user'?'你':'小伴';
  const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=text;item.append(name,bubble);
  if(image){const img=document.createElement('img');img.className='chat-photo';img.alt='本次提供给小伴的照片';img.src='data:image/jpeg;base64,'+image;item.append(img);}
  $('messages').append(item);scroll();return{item,bubble};
}
function replayButton(item,text){const b=document.createElement('button');b.className='replay';b.textContent='▷ 再听一遍';b.onclick=()=>speak(text);item.append(b);}
function render(){$('messages').replaceChildren();if(!messages.length)addMessage('assistant',welcome);for(const m of messages){const row=addMessage(m.role,m.content);if(m.role==='assistant')replayButton(row.item,m.content);}$('suggestions').hidden=messages.length>0;}
async function api(path,body,type='application/json',signal=null,extraHeaders={}){
  const res=await fetch(path,{method:'POST',signal,headers:{'Content-Type':type,...extraHeaders},body:type==='application/json'?JSON.stringify(body):body});
  if(!res.ok){const data=await res.json();throw new Error(data.error||'请求失败');}return res;
}
function stopAudio(){speechVersion++;speaking=false;if(audio){audio.pause();audio=null;}if(audioUrl){URL.revokeObjectURL(audioUrl);audioUrl=null;}}
function newTurnId(){return crypto.randomUUID();}
function cancelTurn({preserveSharedVoice=false,preserveTrace=false}={}){
  if(typeof stopSpeechPreview==='function')stopSpeechPreview(false);
  if(typeof personalMemoryCancel==='function')personalMemoryCancel();
  if(!preserveTrace)conversationDiagnostics.finish(conversationDiagnostics.current(),'cancelled','stopped');
  if(!preserveSharedVoice&&typeof sharedVoiceCancelPending==='function')sharedVoiceCancelPending();
  turnVersion++;chatAbort?.abort();chatAbort=null;ttsAbort?.abort();ttsAbort=null;
  const previous=turnId;turnId=null;
  if(previous)api('/api/turn/cancel',{turn_id:previous}).catch(()=>{});
  stopAudio();busy=false;
  if(activeRow){activeRow.bubble.textContent=(activeRow.bubble.textContent==='…'?'':activeRow.bubble.textContent)+'\n（已打断）';activeRow=null;}
  controls();
}
async function returnToWake(){await pauseWake();await resumeWake();}
async function monitorInterrupt(){
  if(!wakeEnabled||!$('bargeIn').checked||enrolling||wakeListener)return;
  await resumeWake('interrupt');
}
async function speak(text,{onComplete=null,onFailure=null,traceId=null}={}){
  if(typeof stopSpeechPreview==='function')stopSpeechPreview(false);
  if(!ttsReady){conversationDiagnostics.finish(traceId,'done','unavailable');notice('当前未启用语音播放，可以继续文字对话。');return;}
  if(busy||recording||wakePhase==='command'||enrolling)return;
  traceId=traceId&&conversationDiagnostics.isLive(traceId)?traceId:conversationDiagnostics.start('replay');
  conversationDiagnostics.stage(traceId,'tts');
  ttsAbort?.abort();stopAudio();const version=speechVersion;
  const id=turnId||newTurnId();turnId=id;const controller=new AbortController();ttsAbort=controller;
  speaking=true;
  if(!(wakeEnabled&&$('bargeIn').checked&&wakeListener?.mode==='interrupt'))await pauseWake();
  notice();
  try{
    if(version!==speechVersion)return;
    await monitorInterrupt();if(version!==speechVersion)return;
    state('正在准备声音…');const res=await api('/api/speak',{text,turn_id:id},'application/json',controller.signal,conversationDiagnostics.headers(traceId));
    const blob=await res.blob();conversationDiagnostics.refresh(traceId);if(version!==speechVersion)return;
    audioUrl=URL.createObjectURL(blob);audio=new Audio(audioUrl);
    audio.onended=async()=>{if(version===speechVersion){stopAudio();if(onComplete)await onComplete();else{conversationDiagnostics.finish(traceId);returnToWake();}}};
    audio.onerror=()=>{if(version===speechVersion){conversationDiagnostics.finish(traceId,'error','playback');stopAudio();if(onFailure)onFailure();notice('声音播放失败，可以点击「再听一遍」。');returnToWake();}};
    conversationDiagnostics.stage(traceId,'playback');await audio.play();if(version===speechVersion)state(wakeEnabled&&$('bargeIn').checked?'正在说给你听，可以插话…':'正在说给你听…','speaking');
  }catch(error){if(version===speechVersion&&error.name!=='AbortError'){conversationDiagnostics.finish(traceId,'error',audio?'playback':'tts');conversationDiagnostics.refresh(traceId);stopAudio();if(onFailure)onFailure();notice('声音未播放：'+error.message);returnToWake();}}
  finally{conversationDiagnostics.refresh(traceId);if(ttsAbort===controller)ttsAbort=null;}
}

const actionFrames={
  nod:[{transform:'rotateX(0deg) translateY(0)'},{transform:'rotateX(24deg) translateY(10px)'},{transform:'rotateX(0deg) translateY(0)'},{transform:'rotateX(24deg) translateY(10px)'},{transform:'rotateX(0deg) translateY(0)'}],
  shake:[{transform:'rotate(0deg)'},{transform:'rotate(-14deg)'},{transform:'rotate(14deg)'},{transform:'rotate(-14deg)'},{transform:'rotate(0deg)'}],
  left:[{transform:'translateX(0) rotateY(0)'},{transform:'translateX(-24px) rotateY(-25deg)',offset:.35},{transform:'translateX(-24px) rotateY(-25deg)',offset:.75},{transform:'translateX(0) rotateY(0)'}],
  right:[{transform:'translateX(0) rotateY(0)'},{transform:'translateX(24px) rotateY(25deg)',offset:.35},{transform:'translateX(24px) rotateY(25deg)',offset:.75},{transform:'translateX(0) rotateY(0)'}],
  happy:[{transform:'translateY(0) rotate(0)'},{transform:'translateY(-17px) rotate(-7deg)'},{transform:'translateY(0) rotate(7deg)'},{transform:'translateY(-17px) rotate(-7deg)'},{transform:'translateY(0) rotate(0)'}],
  reset:[{transform:'translate(0) rotate(0)'},{transform:'translate(0) rotate(0)'}]
};
function animateAction(event){
  if(event.target!=='screen'||!actionFrames[event.action])return;
  motion?.cancel();clearTimeout(motionTimer);$('robot').classList.toggle('happy',event.action==='happy');
  $('actionStatus').textContent=`屏幕动作：${event.label} · 实体机器人未连接`;
  const duration=window.matchMedia('(prefers-reduced-motion: reduce)').matches?1:Math.min(2400,Math.max(200,event.duration_ms));
  motion=$('robot').animate(actionFrames[event.action],{duration,easing:'ease-in-out'});
  motionTimer=setTimeout(()=>$('robot').classList.remove('happy'),duration);
}
async function runAction(action){try{const event=await(await api('/api/action',{action})).json();animateAction(event);}catch(error){notice(error.message);}}
function isVisualRequest(text){
  const compact=text.replace(/[\s，。！？!?,.]/g,'');
  // "看看能不能认出我的声音" asks about voice identity, not the camera.
  if(/声纹|声音|认出我|认识我|我是谁|识别我/.test(text)&&!/照片|图片|摄像头|镜头|拍照|画面/.test(text))return false;
  if(/^(?:小伴|你好小伴)?(?:请你|请|给我|帮我|你)?(?:向|往)?[左右](?:边)?(?:看|转)(?:一下|看|头|吧)?$/.test(compact))return false;
  return !/(不要|别|不许).{0,5}(看|拍)/.test(text)&&/(帮我看|给我看一下|看看|看一看|看下|你看|拍[一张照]|镜头|摄像头|手里.*什么|眼前.*什么)/.test(text);
}
function takePhoto(){
  const video=$('camera');if(!cameraStream||video.readyState<2||!video.videoWidth)throw new Error('请先开启摄像头，等画面出现后再拍照。');
  const scale=Math.min(1,768/Math.max(video.videoWidth,video.videoHeight));const canvas=document.createElement('canvas');
  canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
  return canvas.toDataURL('image/jpeg',.82).split(',')[1];
}
async function send(text,{image=null,source='text',traceId=null}={}){
  text=text.trim();if(!text||busy||!ready||recording||enrolling)return;
  if(typeof sharedVoiceActive==='function'&&sharedVoiceActive()){notice('当前是共同记忆语音模式，请使用麦克风；文字输入不能代替口头确认。');return;}
  traceId=traceId&&conversationDiagnostics.isLive(traceId)?traceId:conversationDiagnostics.start(source);
  if(source==='text'&&typeof speakerDiagnostic==='function')speakerDiagnostic('文字发送',{'说明':'此按钮只发送文字，不采集声音，也不会重新匹配声纹。','声纹开关':$('speakerAuto').checked?'已开启，但本次没有音频':'关闭'});
  if(!image&&isVisualRequest(text)){
    if(!cameraStream){conversationDiagnostics.finish(traceId,'error','camera');notice('请先点击「开启摄像头」，再说「帮我看看」或点击拍照。');if(typeof speakerDiagnostic==='function')speakerDiagnostic('问题未发送',{'原因':'被识别为看图请求，但摄像头未开启。'});resumeWake();return;}
    if(!visionReady){conversationDiagnostics.finish(traceId,'error','camera');notice('视觉模型还未准备好，先聊点别的吧。');resumeWake();return;}
    try{image=takePhoto();}catch(error){conversationDiagnostics.finish(traceId,'error','camera');notice(error.message);resumeWake();return;}
  }
  cancelTurn({preserveTrace:true});conversationDiagnostics.stage(traceId,'request');const version=turnVersion,id=newTurnId();turnId=id;const controller=new AbortController();chatAbort=controller;
  busy=true;controls();await pauseWake();if(version!==turnVersion)return;notice();$('input').value='';$('suggestions').hidden=true;
  messages.push({role:'user',content:text});addMessage('user',text,image);persist();
  const row=addMessage('assistant','');activeRow=row;state(image?'让我仔细看看…':'让我想一想…','thinking');
  const greeting=typeof voiceGreeting==='function'?voiceGreeting(source):null;
  let answer='',completed=false;const started=performance.now();
  try{
    await monitorInterrupt();if(version!==turnVersion)return;
    if(typeof speakerDiagnostic==='function')speakerDiagnostic('问题已提交',{'输入方式':source==='text'?'文字':source==='wake'?'语音唤醒 / 插话':'点击录音','当前档案':activeProfile?'已选择或匹配用户':'访客'});
    const res=await api('/api/chat',{messages:messages.slice(-16),profile_id:activeProfile,turn_id:id,...(image?{image}:{})},'application/json',controller.signal,conversationDiagnostics.headers(traceId));const reader=res.body.getReader(),decoder=new TextDecoder();let pending='';
    function consume(line){if(version!==turnVersion||!line.trim())return;const data=JSON.parse(line);if(data.error)throw new Error(data.error);if(data.memory_changed&&typeof refreshMemories==='function')refreshMemories();if(data.memory_review&&typeof showMemoryReview==='function')showMemoryReview(data.memory_review);if(data.action)animateAction(data.action);if(data.personal_memory&&typeof renderPersonalMemoryContext==='function')renderPersonalMemoryContext(row,data.personal_memory);const piece=data.message?.content||'';if(piece&&!answer&&greeting){answer=greeting.text;markVoiceGreeting(greeting);}answer+=piece;row.bubble.textContent=answer||'…';if(data.done)completed=true;scroll();}
    while(true){const{value,done}=await reader.read();if(version!==turnVersion){await reader.cancel();return;}if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split('\n');pending=lines.pop();for(const line of lines)consume(line);}
    pending+=decoder.decode();if(pending.trim())consume(pending);if(!completed||!answer.trim())throw new Error('回答中断了，请再试一次。');
    if(version!==turnVersion)return;activeRow=null;
    messages.push({role:'assistant',content:answer});persist();replayButton(row.item,answer);
    $('connection').textContent=`${image?'看图':'对话'} · 用时 ${((performance.now()-started)/1000).toFixed(1)} 秒`;
  }catch(error){if(version===turnVersion&&error.name!=='AbortError'){conversationDiagnostics.finish(traceId,'error','request');row.bubble.textContent=answer?answer+'\n（回答中断）':'暂时没能回答，请再试一次。';notice(error.message);}}
  finally{conversationDiagnostics.refresh(traceId);if(version===turnVersion){busy=false;activeRow=null;chatAbort=null;controls();idle();}}
  if(version!==turnVersion)return;
  if(completed&&answer&&ttsReady&&$('autoplay').checked)await speak(answer,{traceId});else{conversationDiagnostics.finish(traceId,'done',ttsReady?'muted':'unavailable');returnToWake();}
}
function pcm16(samples){const buffer=new ArrayBuffer(samples.length*2),view=new DataView(buffer);samples.forEach((v,i)=>{v=Math.max(-1,Math.min(1,v));view.setInt16(i*2,v<0?v*32768:v*32767,true);});return new Uint8Array(buffer);}
function encodeWav(chunks,rate){
  const size=chunks.reduce((n,c)=>n+c.length,0),buffer=new ArrayBuffer(44+size*2),view=new DataView(buffer);
  const str=(offset,s)=>[...s].forEach((c,i)=>view.setUint8(offset+i,c.charCodeAt(0)));
  str(0,'RIFF');view.setUint32(4,36+size*2,true);str(8,'WAVE');str(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,rate,true);view.setUint32(28,rate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,size*2,true);
  let offset=44;for(const chunk of chunks){new Uint8Array(buffer,offset,chunk.length*2).set(pcm16(chunk));offset+=chunk.length*2;}return buffer;
}
class Microphone{
  async start(onAudio,onEnded=null){
    this.closed=false;
    try{
      this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
      if(this.closed){this.stream.getTracks().forEach(t=>t.stop());return;}
      const track=this.stream.getAudioTracks()[0],settings=track?.getSettings?.()||{};
      if(track)track.onended=()=>{if(!this.closed&&onEnded)onEnded();};
      if(typeof speakerDiagnostic==='function')speakerDiagnostic('已打开麦克风',{'实际设备':track?.label||'浏览器未提供设备名称','设备采样率':settings.sampleRate||'未知','设备声道数':settings.channelCount||'未知','回声消除':settings.echoCancellation===undefined?'未知':String(settings.echoCancellation)});
      this.context=new AudioContext({sampleRate:16000});await this.context.resume();
      if(this.context.sampleRate!==16000)throw new Error('当前浏览器不支持 16kHz 录音，请使用 Chrome。');
      await this.context.audioWorklet.addModule('/recorder.js');if(this.closed)return;
      this.node=new AudioWorkletNode(this.context,'pcm-recorder');this.node.port.onmessage=e=>{if(!this.closed)onAudio(e.data);};
      this.source=this.context.createMediaStreamSource(this.stream);this.source.connect(this.node);this.node.connect(this.context.destination);
    }catch(error){await this.stop();throw error;}
  }
  async stop(){this.closed=true;this.stream?.getTracks().forEach(t=>t.stop());if(this.node){this.node.port.onmessage=null;this.node.disconnect();}this.source?.disconnect();if(this.context&&this.context.state!=='closed')await this.context.close();}
}
class WakeListener{
  constructor(mode='wake'){this.mode=mode;this.closed=false;this.queue=[];this.pending=[];this.count=0;this.sequence=0;this.sending=false;this.mic=new Microphone();this.traceId=mode==='command'?conversationDiagnostics.current():null;}
  async start(){
    const result=await(await api('/api/listen/start',{mode:this.mode,identify_speaker:$('speakerAuto').checked})).json();this.session=result.session;
    if(this.closed){await this.stop();return;}
    await this.mic.start(chunk=>{
      this.pending.push(chunk);this.count+=chunk.length;
      if(this.count>=4096){const samples=new Float32Array(this.count);let pos=0;for(const p of this.pending){samples.set(p,pos);pos+=p.length;}this.pending=[];this.count=0;this.queue.push(samples);this.pump();}
    },()=>{conversationDiagnostics.finish(this.traceId||conversationDiagnostics.current(),'error','device');wakeEnabled=false;stopInteraction();notice('麦克风已断开，请重新连接并开启语音。');});
  }
  async pump(){
    if(this.sending||this.closed)return;this.sending=true;
    try{
      while(this.queue.length&&!this.closed){
        if(this.queue.length>12)throw new Error('语音处理暂时跟不上，请重新开启唤醒。');
        const bytes=pcm16(this.queue.shift()),pcm=btoa(String.fromCharCode(...bytes));
        const result=await(await api('/api/listen/chunk',{session:this.session,sequence:this.sequence++,pcm},'application/json',null,conversationDiagnostics.headers(this.traceId))).json();
        if(this.closed)return;
        if(result.event==='interrupt'){cancelTurn();this.traceId=conversationDiagnostics.start('interrupt');conversationDiagnostics.stage(this.traceId,'listening');wakePhase='command';state('已打断，我在听…','listening');controls();}
        else if(result.event==='wake'){this.traceId=conversationDiagnostics.start('wake');conversationDiagnostics.stage(this.traceId,'listening');wakePhase='command';state('我在听，请说你的问题…','listening');$('wakeHint').textContent='说完停顿约一秒，自动发送 · 最长 30 秒';animateAction({action:'nod',label:'听到你啦',target:'screen',duration_ms:800});controls();}
        else if(result.event==='listening'){if(this.mode==='command'&&!this.confirmStarted&&result.speech){this.confirmStarted=true;this.traceId=conversationDiagnostics.start('confirmation');conversationDiagnostics.stage(this.traceId,'listening');}state(result.speech?'我在听，说完会自动发送…':'我在听，请说你的问题…','listening');}
        else if(result.event==='utterance'||result.event==='timeout'){
          const traceId=this.traceId;conversationDiagnostics.audioResult(traceId,result);conversationDiagnostics.refresh(traceId);
          await pauseWake();
          if(typeof transcriptionDiagnostic==='function')transcriptionDiagnostic(result,'wake');
          if(typeof sharedVoiceActive==='function'&&sharedVoiceActive()){
            await sharedVoiceReceive(result,'wake',traceId);
            return;
          }
          if(result.speaker&&typeof applySpeaker==='function')applySpeaker(result.speaker);
          if(speakerRetryNeeded(result,traceId)){
            await resumeWake();
            return;
          }
          if(result.text)await send(result.text,{source:'wake',traceId});
          else{
            conversationDiagnostics.finish(traceId,'empty','empty');
            notice(result.event==='timeout'?'没有听到问题，已回到唤醒待机。':'没有听清，请再叫我一次。');
            resumeWake();
          }
          return;
        }
      }
    }catch(error){if(!this.closed){conversationDiagnostics.finish(this.traceId||conversationDiagnostics.current(),'error','recognition');if(typeof speakerDiagnostic==='function')speakerDiagnostic('唤醒处理失败',{'原因':error.message});wakeEnabled=false;await pauseWake();notice('语音唤醒已暂停：'+error.message);idle();controls();}}
    finally{this.sending=false;}
  }
  async stop(){this.closed=true;this.queue=[];this.pending=[];await this.mic.stop();if(this.session){const session=this.session;this.session=null;try{await api('/api/listen/stop',{session});}catch{}}}
}
async function pauseWake(){const current=wakeListener;wakeListener=null;wakePhase='';if(current)await current.stop();controls();}
async function resumeWake(mode='wake'){
  if(typeof speechPreviewActive==='function'&&speechPreviewActive())return;
  idle();if(!wakeEnabled||enrolling||recording||wakeListener||wakeStarting)return;
  if(mode==='wake'&&(busy||speaking))return;
  wakeStarting=true;controls();const listener=new WakeListener(mode);wakeListener=listener;
  try{
    await listener.start();if(wakeListener!==listener||!wakeEnabled){await listener.stop();return;}
    // An interrupt may already have promoted the session to command during start.
    if(wakePhase!=='command')wakePhase=mode;
    $('wakeHint').textContent=mode==='interrupt'?'正在监听插话 · 可直接说出新问题':'正在本机监听「你好小伴」';idle();
  }catch(error){if(wakeListener===listener){const current=conversationDiagnostics.current();if(current)conversationDiagnostics.warn(current,'mic');else{const failed=conversationDiagnostics.start('wake');conversationDiagnostics.stage(failed,'capture');conversationDiagnostics.finish(failed,'error','mic');}wakeEnabled=false;await pauseWake();notice(micError(error));idle();}}
  finally{wakeStarting=false;controls();}
}

function micError(error){return error.name==='NotAllowedError'?'请在浏览器地址栏允许麦克风权限，也可以先打字体验。':error.message;}
async function startRecording(){
  if(!speaking&&!busy&&typeof sharedVoiceState!=='undefined'&&sharedVoiceState.pending)conversationDiagnostics.finish(conversationDiagnostics.current());
  cancelTurn({preserveSharedVoice:!speaking&&!busy});
  const version=turnVersion;busy=true;controls();await pauseWake();if(version!==turnVersion)return;
  notice();const mic=new Microphone();manual=mic;frames=[];const traceId=conversationDiagnostics.start('manual');mic.traceId=traceId;conversationDiagnostics.stage(traceId,'capture');
  try{
    await mic.start(chunk=>{if(version===turnVersion)frames.push(chunk);},()=>{conversationDiagnostics.finish(traceId,'error','device');stopInteraction();notice('麦克风已断开，请重新连接后录音。');});
    if(version!==turnVersion){await mic.stop();return;}
    conversationDiagnostics.stage(traceId,'listening');recording=true;state('我在听，慢慢说…','listening');$('mic').querySelector('span').textContent='结束并发送';
    const start=Date.now();tick=setInterval(()=>$('micHint').textContent='录音中 '+Math.floor((Date.now()-start)/1000)+' / 30 秒',200);timer=setTimeout(stopRecording,30000);
  }catch(error){await mic.stop();if(version===turnVersion){conversationDiagnostics.finish(traceId,'error','mic');notice(micError(error));if(typeof speakerDiagnostic==='function')speakerDiagnostic('无法开始录音',{'原因':micError(error)});}}
  finally{if(version===turnVersion){busy=false;controls();if(!recording)resumeWake();}}
}
async function stopRecording(){
  if(!recording)return;
  const version=turnVersion,captured=frames,mic=manual,traceId=mic?.traceId;frames=[];manual=null;
  conversationDiagnostics.stage(traceId,'recognizing');
  recording=false;busy=true;controls();clearInterval(tick);clearTimeout(timer);await mic.stop();
  if(version!==turnVersion)return;
  $('mic').querySelector('span').textContent='点击说话';$('micHint').textContent='点击开始，再次点击结束 · 最长 30 秒';state('正在听懂你说的话…','thinking');
  try{
    const result=await(await api('/api/transcribe',encodeWav(captured,16000),'audio/wav',null,{'X-Speaker-Recognition':String($('speakerAuto').checked),...conversationDiagnostics.headers(traceId)})).json();
    if(version!==turnVersion)return;
    conversationDiagnostics.audioResult(traceId,result);conversationDiagnostics.refresh(traceId);
    if(typeof transcriptionDiagnostic==='function')transcriptionDiagnostic(result,'manual');
    if(typeof sharedVoiceActive==='function'&&sharedVoiceActive()){busy=false;controls();await sharedVoiceReceive(result,'manual',traceId);return;}
    if(result.speaker&&typeof applySpeaker==='function')applySpeaker(result.speaker);
    busy=false;controls();
    if(speakerRetryNeeded(result,traceId)){await resumeWake();return;}
    if(result.text)send(result.text,{source:'manual',traceId});else{conversationDiagnostics.finish(traceId,'empty','empty');notice('没有听清，请再说一次。');resumeWake();}
  }catch(error){if(version===turnVersion){conversationDiagnostics.finish(traceId,'error','recognition');conversationDiagnostics.refresh(traceId);if(typeof speakerDiagnostic==='function')speakerDiagnostic('录音处理失败',{'原因':error.message});busy=false;controls();notice(error.message);resumeWake();}}
}
async function stopInteraction(){
  cancelTurn();
  if(enrolling&&typeof cancelEnrollment==='function')await cancelEnrollment();
  if(recording){
    recording=false;clearInterval(tick);clearTimeout(timer);const mic=manual;manual=null;frames=[];await mic?.stop();
    $('mic').querySelector('span').textContent='点击说话';$('micHint').textContent='点击开始，再次点击结束 · 最长 30 秒';
  }
  await returnToWake();controls();
}

function closeCamera(){cameraVersion++;cameraStream?.getTracks().forEach(t=>t.stop());cameraStream=null;$('camera').srcObject=null;$('cameraPreview').hidden=true;$('cameraToggle').textContent='开启摄像头';$('cameraToggle').setAttribute('aria-pressed','false');$('cameraHint').textContent='开启后说「帮我看看这是什么」，或点击拍照。';controls();}
async function toggleCamera(){
  if(cameraStream){closeCamera();return;}cameraStarting=true;controls();notice();const version=++cameraVersion;let opened=null;
  try{opened=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720},facingMode:'user'},audio:false});if(version!==cameraVersion){opened.getTracks().forEach(t=>t.stop());return;}cameraStream=opened;$('camera').srcObject=opened;await $('camera').play();$('cameraPreview').hidden=false;$('cameraToggle').textContent='关闭摄像头';$('cameraToggle').setAttribute('aria-pressed','true');$('cameraHint').textContent=visionReady?'说「帮我看看」，或拍照并发送输入框里的问题。':'画面已就绪，视觉模型准备中。';}
  catch(error){opened?.getTracks().forEach(t=>t.stop());closeCamera();notice(error.name==='NotAllowedError'?'请在浏览器地址栏允许摄像头权限。':error.message);}
  finally{cameraStarting=false;controls();}
}
$('chatForm').onsubmit=e=>{e.preventDefault();send($('input').value);};
$('input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send($('input').value);}};
$('mic').onclick=()=>recording?stopRecording():startRecording();
$('wake').onclick=async()=>{wakeEnabled=!wakeEnabled;if(wakeEnabled){if(speaking)cancelTurn();stopAudio();await resumeWake();}else{if(wakePhase==='command')conversationDiagnostics.finish(conversationDiagnostics.current(),'cancelled','stopped');await pauseWake();$('wakeHint').textContent='语音唤醒已关闭 · 麦克风已释放';idle();controls();}};
$('stopAudio').onclick=stopInteraction;
$('bargeIn').onchange=async()=>{if(busy||speaking){await pauseWake();await monitorInterrupt();}};$('autoplay').onchange=()=>{if(!$('autoplay').checked){if(speaking)conversationDiagnostics.finish(conversationDiagnostics.current(),'cancelled','muted');ttsAbort?.abort();stopAudio();returnToWake();}};
$('clear').onclick=()=>{if(typeof sharedVoiceActive==='function'&&sharedVoiceActive()){cancelTurn();$('messages').replaceChildren();notice();resumeWake();return;}cancelTurn();if(typeof resetVoiceGreetings==='function')resetVoiceGreetings();messages=[];persist();render();notice();resumeWake();};
$('cameraToggle').onclick=toggleCamera;$('capture').onclick=()=>{try{send($('input').value.trim()||'请描述这张照片里你看到了什么。',{image:takePhoto()});}catch(error){notice(error.message);}};
 document.querySelectorAll('#suggestions button').forEach(b=>b.onclick=()=>send(b.textContent));
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>runAction(b.dataset.action));
window.addEventListener('pagehide',()=>{wakeEnabled=false;cancelTurn();wakeListener?.stop();manual?.stop();if(typeof cancelEnrollment==='function')cancelEnrollment();closeCamera();});
async function status(){
  try{const result=await(await fetch('/api/status')).json();ready=result.chat;asrReady=result.asr;wakeReady=result.wake;visionReady=result.vision;
    $('modelPrivacy').textContent=result.remote_model?'远端模型回答 · 对话与相关记忆会发送到模型服务':'本机模型回答 · 按需使用麦克风与摄像头';
    speakerReady=result.capabilities?.speaker===true;
    ttsReady=result.tts!==false;$('autoplay').disabled=!ttsReady;if(!ttsReady)$('autoplay').checked=false;
    $('connection').textContent=ready?`${result.remote_model?'远端':'本机'}对话已就绪 · ${result.model||''} · ${visionReady?'看图已就绪':'看图未启用或未就绪'}`:'聊天模型未就绪，正在检查连接';idle();controls();if(!ready)setTimeout(status,5000);
  }catch{state('服务未连接，请运行启动脚本');setTimeout(status,5000);}
}
render();status();
