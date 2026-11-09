// Only stage codes, durations and bounded numeric audio metadata are retained.
// No utterances, account names, memory text, voiceprint vectors or credentials.
const conversationDiagnostics=(()=>{
  const labels={capture:'打开麦克风',listening:'收音 / 断句与提交',recognizing:'语音识别与声纹处理',request:'提交问题',shared:'共同记忆处理',asr:'语音转文字',speaker:'声纹匹配',routing:'识别意图 / 规则处理',access:'共同记忆权限 / 指令处理',memory:'检索相关记忆',model_wait:'等待模型首段回答',model:'生成回答',tts:'合成声音',playback:'播放声音',confirmation:'等待口头确认'};
  const outcomes={running:'进行中',done:'完成',cancelled:'已打断 / 取消',error:'失败',empty:'没有听到有效问题'};
  const reasons={mic:'检查麦克风权限、连接和系统输入设备。',device:'麦克风连接已中断，请重新连接后录音。',recognition:'语音识别请求失败，请检查服务；声纹问题可展开下方声音识别诊断。',empty:'没有识别到文字，请靠近麦克风说一句完整的话。',request:'请求未完成，请检查本地服务与模型连接。',playback:'浏览器未能播放声音，请检查播放权限和输出设备，再点“再听一遍”。',tts:'声音合成失败，请检查语音服务。',muted:'自动朗读已关闭，本轮仅显示文字。',unavailable:'语音服务未启用，本轮仅显示文字。',camera:'看图请求未发送，请先开启摄像头并确认视觉模型就绪。',confirmation:'确认等待已结束，请重新提出保存或修改。',shared:'共同记忆处理未完成，请检查账号、说话人和空间权限；若提交后断线，请先核对是否已保存。',superseded:'已开始下一轮。',stopped:'用户停止了本轮。'};
  const sourceLabels={text:'文字',manual:'点击录音',wake:'唤醒',interrupt:'插话',confirmation:'口头确认',replay:'再次朗读',shared:'共同语音提示'};
  const rows=[];let active=null,selected=null,polling=false;
  const el=id=>document.getElementById(id);
  const now=()=>performance.now();
  const find=id=>rows.find(r=>r.id===id);
  const live=id=>{const r=find(id);return r?.outcome==='running'?r:null;};
  function closeStep(r,status='done'){
    const s=r.steps.at(-1);if(s?.status==='running'){s.status=status;s.elapsed_ms=Math.round(now()-s.start);}
  }
  function start(source){
    if(live(active))finish(active,live(active).stage==='confirmation'?'done':'cancelled','superseded');
    const id=crypto.randomUUID();rows.unshift({id,source:sourceLabels[source]?source:'text',time:new Date().toISOString(),start:now(),outcome:'running',stage:null,steps:[],backend:[],audio:{},reason:null});
    rows.splice(20);active=selected=id;render();return id;
  }
  function stage(id,key){
    const r=live(id);if(!r||!labels[key])return;
    closeStep(r);r.stage=key;r.steps.push({stage:key,status:'running',start:now()});render();
  }
  function finish(id,outcome='done',reason=null){
    const r=live(id);if(!r)return;
    closeStep(r,outcome==='error'?'error':outcome==='cancelled'?'cancelled':'done');
    r.outcome=outcomes[outcome]?outcome:'error';r.elapsed_ms=Math.round(now()-r.start);r.reason=reasons[reason]?reason:null;
    if(active===id)active=null;render();
  }
  function warn(id,reason){const r=live(id);if(r&&reasons[reason]){r.warning=reason;render();}}
  function audioResult(id,result){
    const r=live(id);if(!r)return;
    const d=result.diagnostics||{};
    for(const k of ['audio_seconds','speech_seconds','rms_dbfs','top_score','threshold','score_gap'])if(Number.isFinite(d[k]))r.audio[k]=d[k];
    r.audio.speaker=d.speaker_enabled===false?'disabled':result.speaker?.matched?'matched':result.speaker?'unmatched':'not_run';
    r.audio.text_detected=!!result.text;render();
  }
  async function refresh(id){
    const r=find(id);if(!r)return;const sequence=r.pollSequence=(r.pollSequence||0)+1;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
    try{
      const response=await fetch('/api/diagnostics/trace',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({trace_id:id})});
      if(!response.ok)throw new Error('unavailable');
      const data=await response.json();if(find(id)!==r||r.pollSequence!==sequence)return;
      r.backend=(Array.isArray(data.steps)?data.steps:[]).slice(-64).filter(s=>labels[s.stage]&&['running','done','error','cancelled'].includes(s.status)&&Number.isFinite(s.elapsed_ms)).map(s=>({stage:s.stage,status:s.status,elapsed_ms:Math.max(0,s.elapsed_ms)}));
      r.traceAvailable=data.available===true;r.lastPoll=now();render();
    }catch{if(find(id)===r&&r.pollSequence===sequence){r.traceAvailable=false;render();}}finally{clearTimeout(timeout);}
  }
  function currentLabel(r){
    const server=r.backend.findLast(s=>s.status==='running');
    return labels[(['listening','recognizing','request','shared','tts'].includes(r.stage)&&server)?server.stage:r.stage]||'准备中';
  }
  function duration(ms){return (Math.max(0,ms)/1000).toFixed(1)+' 秒';}
  function exportData(){return {version:1,scope:'本页面最近20轮；不含正文、身份与录音',turns:rows.map(r=>({id:r.id,source:r.source,time:r.time,outcome:r.outcome,reason:r.reason,warning:r.warning||null,elapsed_ms:r.elapsed_ms??Math.round(now()-r.start),audio:{...r.audio},steps:r.steps.map(s=>({stage:s.stage,status:s.status,elapsed_ms:s.elapsed_ms??Math.round(now()-s.start)})),backend:r.backend.map(s=>({...s}))}))};}
  function render(){
    if(!el('conversationTraceList'))return;
    const current=live(active),last=rows[0];
    el('conversationState').textContent=current?currentLabel(current):last?(outcomes[last.outcome]+' · 等待下一次输入'):'待机 · 等待输入';
    el('conversationTraceList').replaceChildren();
    for(const r of rows){const b=document.createElement('button');b.type='button';b.className='trace-turn'+(selected===r.id?' selected':'');b.textContent=new Date(r.time).toLocaleTimeString('zh-CN',{hour12:false})+' · '+sourceLabels[r.source]+' · '+(r.outcome==='running'?currentLabel(r):outcomes[r.outcome]);b.setAttribute('aria-pressed',String(selected===r.id));b.onclick=()=>{selected=r.id;render();};el('conversationTraceList').append(b);}
    const r=find(selected);el('conversationTraceDetail').replaceChildren();if(!r)return;
    const add=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;el('conversationTraceDetail').append(node);};
    add('h3',sourceLabels[r.source]+' · '+outcomes[r.outcome]);
    add('p','轮次 '+r.id.slice(0,8)+' · 总耗时 '+duration(r.elapsed_ms??now()-r.start));
    if(r.outcome==='running')add('p','当前：'+currentLabel(r)+(now()-(r.steps.at(-1)?.start??r.start)>15000?'。等待较久；可以点击“打断 / 停止”后重试。':'。'));
    if(r.reason)add('p',reasons[r.reason]);
    if(r.warning)add('p','提示：'+reasons[r.warning]);
    add('h4','页面流程（依次发生）');
    for(const s of r.steps)add('p',(labels[s.stage]||s.stage)+' · '+outcomes[s.status]+' · '+duration(s.elapsed_ms??now()-s.start));
    if(r.backend.length){add('h4','服务端细分（包含在上方页面阶段中，不重复相加）');for(const s of r.backend)add('p',labels[s.stage]+' · '+outcomes[s.status]+' · '+duration(s.elapsed_ms)+(s.status==='running'&&r.outcome!=='running'?' · 最后一次观测，可能仍在收尾':''));}
    else add('p','尚无服务端细分记录；本轮可能还未提交，或诊断服务暂不可用。');
    if(r.audio.speaker)add('p','声纹：'+({disabled:'未开启',matched:'匹配到已登记档案',unmatched:'未确认身份，按访客处理',not_run:'本轮未执行'}[r.audio.speaker])+'；识别文字：'+(r.audio.text_detected?'有':'无'));
    if(Number.isFinite(r.audio.audio_seconds))add('p','录音时长：'+r.audio.audio_seconds+' 秒'+(Number.isFinite(r.audio.speech_seconds)?'；有效语音：'+r.audio.speech_seconds+' 秒':''));
    if(r.traceAvailable===false)add('p','服务端诊断暂不可用或记录已过期；页面阶段记录仍可查看。');
  }
  el('conversationTraceClear').onclick=()=>{const current=live(active);rows.splice(0,rows.length,...(current?[current]:[]));active=selected=current?.id||null;render();};
  el('conversationTraceExport').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(exportData(),null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='yiban-conversation-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  setInterval(async()=>{
    const r=live(active);
    if(r?.stage==='confirmation'&&now()-r.steps.at(-1).start>90000){finish(r.id,'cancelled','confirmation');return;}
    if(r&&['listening','recognizing','request','shared','tts'].includes(r.stage)&&!polling){polling=true;try{await refresh(r.id);}finally{polling=false;}}
    if(el('conversationDiagnosticsPanel').open)render();
  },1200);
  render();return {start,stage,finish,warn,audioResult,refresh,exportData,current:()=>active,isLive:id=>!!live(id),headers:id=>id?{'X-Conversation-Trace':id}:{}};
})();
