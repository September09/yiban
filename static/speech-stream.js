// One utterance, one generation and one output stream. PCM frames are never
// independent TTS requests: a single decoder produces every frame in order.
class ContinuousSpeechPlayer{
  constructor({onStart=()=>{},onPlayer=()=>{}}={}){
    this.onStart=onStart;this.onPlayer=onPlayer;this.sources=new Set();
    this.stopped=false;this.ended=false;this.sequence=0;this.total=0;this.rate=null;this.underruns=0;
    this.done=new Promise(resolve=>{this.resolve=resolve;});
  }
  async init(){
    if(this.context)return;
    const Context=window.AudioContext||window.webkitAudioContext;
    if(!Context)throw new Error('浏览器不支持连续音频播放。');
    this.context=new Context({latencyHint:'interactive'});
    this.player=new Audio();
    this.player.onerror=()=>this.stop(new Error('连续声音播放失败。'));
    this.onPlayer(this.player);
    if(typeof audioDevices!=='undefined')await audioDevices.routeOutput(this.player);
    if(this.stopped)return;
    // Avoid the extra MediaStream -> media-element playout buffer when the
    // context can use the already-validated output route directly.
    const sink=this.player.sinkId||'';
    this.bridged=!!sink&&typeof this.context.setSinkId!=='function';
    if(this.bridged){this.destination=this.context.createMediaStreamDestination();this.player.srcObject=this.destination.stream;}
    else{if(sink)await this.context.setSinkId(sink);this.destination=this.context.destination;}
    if(this.stopped)return;
    await this.context.resume();
  }
  async push(frame){
    if(this.stopped)return;
    if(frame.sequence!==this.sequence||frame.format!=='f32le'||!Number.isInteger(frame.samples)||frame.samples<=0||frame.samples>192000||![16000,22050,24000,44100,48000].includes(frame.sample_rate)||(this.rate!==null&&this.rate!==frame.sample_rate))throw new Error('连续音频顺序或格式不正确。');
    const binary=atob(frame.audio);if(binary.length!==frame.samples*4)throw new Error('连续音频长度不正确。');
    const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0)),view=new DataView(bytes.buffer),pcm=new Float32Array(frame.samples);
    for(let i=0;i<pcm.length;i++){const n=view.getFloat32(i*4,true);if(!Number.isFinite(n)||Math.abs(n)>1)throw new Error('连续音频数据不正确。');pcm[i]=n;}
    await this.init();if(this.stopped)return;
    const context=this.context,buffer=context.createBuffer(1,pcm.length,frame.sample_rate);buffer.copyToChannel(pcm,0);
    const source=context.createBufferSource();source.buffer=buffer;source.connect(this.destination);
    const first=this.sequence===0;
    if(first)this.nextTime=context.currentTime+.12;
    else if(this.nextTime<context.currentTime+.01){this.underruns++;this.nextTime=context.currentTime+.08;}
    const start=this.nextTime;this.nextTime+=buffer.duration;
    this.sources.add(source);source.onended=()=>{this.sources.delete(source);source.disconnect();this.settle();};
    source.start(start);this.sequence++;this.total+=frame.samples;this.rate=frame.sample_rate;
    if(first){
      if(this.bridged)await this.player.play();if(this.stopped)return;
      if(typeof audioDevices!=='undefined')audioDevices.playbackStarted(this.player);
      this.firstTimer=setTimeout(()=>{if(!this.stopped)this.onStart();},Math.max(0,(start-context.currentTime)*1000));
    }
  }
  finish(packet){
    if(packet.model_calls!==1||packet.chunks!==this.sequence||packet.total_samples!==this.total||!this.total)throw new Error('声音未完整接收，请重新朗读。');
    this.ended=true;this.settle();return this.done;
  }
  settle(){if(this.ended&&!this.sources.size&&!this.stopped){this.resolve(true);this.dispose();}}
  dispose(){
    clearTimeout(this.firstTimer);
    if(this.player){if(typeof audioDevices!=='undefined')audioDevices.releaseOutput(this.player);this.player.pause();this.player.srcObject=null;}
    this.destination?.stream?.getTracks().forEach(track=>track.stop());
    this.context?.close().catch(()=>{});
  }
  stop(error=null){
    if(this.stopped)return;this.stopped=true;this.error=error;
    for(const source of this.sources){source.onended=null;try{source.stop();}catch{}source.disconnect();}
    this.sources.clear();this.resolve(false);this.dispose();
  }
}

function openContinuousReplySpeech(version,traceId,{finishTrace=true}={}){
  let ended=false,failed=false,player=null,controller=null,reader=null;
  const alive=()=>version===turnVersion&&!controller?.signal.aborted;
  return {
    get failed(){return failed;},
    // Wait for the completed/validated answer, then send exactly ONE utterance.
    feed(){},
    async end(text){
      if(ended||!alive())return false;ended=true;
      if(!text?.trim()){if(finishTrace)conversationDiagnostics.finish(traceId);return true;}
      controller=new AbortController();ttsAbort=controller;
      stopAudio();speaking=true;let receivedDone=false;
      try{
        if(!(wakeEnabled&&$('bargeIn').checked&&wakeListener?.mode==='interrupt'))await pauseWake();
        if(!alive())return false;
        await monitorInterrupt();if(!alive())return false;
        notice();state('正在准备声音…');conversationDiagnostics.stage(traceId,'tts');
        player=new ContinuousSpeechPlayer({onPlayer:p=>{if(alive())audio=p;},onStart:()=>{
          if(!alive()||continuousPlayback!==player)return;
          conversationDiagnostics.stage(traceId,'playback');conversationDiagnostics.playbackStarted(traceId);
          state('正在说给你听…','speaking');
        }});
        continuousPlayback=player;
        controller.signal.addEventListener('abort',()=>player.stop(),{once:true});
        const response=await api('/api/speak/stream',{text,turn_id:turnId||newTurnId()},'application/json',controller.signal,conversationDiagnostics.headers(traceId));
        if(!response.body)throw new Error('浏览器未收到连续音频。');
        reader=response.body.getReader();const decoder=new TextDecoder();let pending='';
        while(alive()){
          const result=await reader.read();if(!alive())break;
          pending+=decoder.decode(result.value||new Uint8Array(),{stream:!result.done});
          let end;
          while((end=pending.indexOf('\n'))>=0){
            const line=pending.slice(0,end);pending=pending.slice(end+1);if(!line.trim())continue;
            const packet=JSON.parse(line);if(packet.error)throw new Error(packet.error);
            if(receivedDone)throw new Error('连续音频结束后出现额外数据。');
            if(packet.type==='audio'){
              if(packet.sequence===0)conversationDiagnostics.audioStream?.(traceId,{received_first:true});
              await player.push(packet);
            }
            else if(packet.type==='status'&&packet.state==='queued'){
              state('声音正在排队，第'+packet.position+'位；可随时打断');
            }
            else if(packet.type==='done'){
              receivedDone=true;player.finish(packet);
              conversationDiagnostics.audioStream?.(traceId,{chunks:player.sequence,model_calls:packet.model_calls,underruns:player.underruns});
            }
            else throw new Error('无法识别连续音频响应。');
            if(player.error)throw player.error;
            if(player.stopped||!alive())return false;
          }
          if(result.done)break;
          if(pending.length>1200000)throw new Error('连续音频响应过长。');
        }
        if(!alive())return false;
        if(!receivedDone||pending.trim())throw new Error('连续音频传输中断，请重新朗读。');
        const ok=await player.done;
        if(player.error)throw player.error;
        if(ok&&alive()&&finishTrace)conversationDiagnostics.finish(traceId);
        return ok&&alive();
      }catch(error){
        if(alive()&&error.name!=='AbortError'){
          failed=true;notice('声音未播放：'+error.message);conversationDiagnostics.finish(traceId,'error','tts');
        }
        controller.abort();return false;
      }finally{
        reader?.cancel().catch(()=>{});
        if(player){
          conversationDiagnostics.audioStream?.(traceId,{chunks:player.sequence,underruns:player.underruns});
          player.stop();if(continuousPlayback===player){continuousPlayback=null;if(audio===player.player)audio=null;speaking=false;}
        }
        else if(alive())speaking=false;
        if(ttsAbort===controller)ttsAbort=null;
        conversationDiagnostics.refresh(traceId);
      }
    }
  };
}
