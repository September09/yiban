/* IP preferences affect presentation and a finite wake vocabulary, never identity/ACLs. */
const branding=(()=>{
  const el=id=>document.getElementById(id);
  const defaults={preset:'default',name:'小伴',primary:'#356b56',accent:'#97b48b',avatar:'default',wake_word:'你好小伴',avatar_url:''};
  const presets={default:defaults,aifeng:{preset:'aifeng',name:'爱凤同学',primary:'#71529d',accent:'#d9a441',avatar:'aifeng',wake_word:'爱凤同学',avatar_url:'/aifeng.svg'}};
  let saved={...defaults},info=null,working=false,previewing=false,uploaded='',clearLogo=false,uploadVersion=0,avatarVersion=0;
  const toneKeys=["142c21b0", "152e2b44", "20382e", "243e38", "274e34", "285540", "304e43", "31593d", "315b35", "324537", "356b56", "356b5620", "376749", "395a42", "416648", "45573906", "47633c", "4c6b43", "60705e", "60785f", "6378511a", "65785d", "667466", "667660", "668257", "687b62", "687c63", "68815c", "68816f", "6e8968", "6e9e70", "70816c", "72816a", "74856b", "768e6120", "778574", "79877d", "7a8970", "7f9075", "81917b", "83927a", "859078", "87a382", "899480", "8aab8c", "8b977f", "8b9787", "8b978b", "8b9b85", "8c987e", "929c8c", "94bca3", "97b48b", "98a08f", "9ab88e", "9bb98d22", "9fb69b", "a1b28e", "a3aa99", "a3aa9b", "afbd9c", "c5cfb455", "c7dfaa", "cbd8c3", "ccd8c9", "d3dfca", "d4efb1", "d5dfd2", "d6e0cf", "d6e9ca", "d8e3cd", "dbe4d3", "dce3d4", "dce3d6", "dce5d4", "dcebd7", "dfe7d7", "dfebdc", "e0e6d9", "e0ebdf", "e0efdf", "e1e6cc", "e2e8dc", "e2eadb", "e3e8dc", "e4e8df", "e4e9df", "e5e8df", "e5e9de", "e6eee3", "e6efdb", "e7eedc", "e7efdf", "e9eee2", "ecf0e4", "edf2e8", "edf3e5", "f0f3e9", "f1f5e9", "f3f5ef", "f4f6ef", "f4f7f0", "f5f7f1", "f6f7f0", "f6f7f2", "f6f8f1", "f8faf5", "fafbf6", "fcffef"];
  function hsl(hex){
    const [r,g,b]=[0,2,4].map(i=>parseInt(hex.slice(i,i+2),16)/255),hi=Math.max(r,g,b),lo=Math.min(r,g,b),delta=hi-lo,l=(hi+lo)/2;
    const s=delta?delta/(1-Math.abs(2*l-1)):0;
    let h=0;if(delta){if(hi===r)h=((g-b)/delta)%6;else if(hi===g)h=(b-r)/delta+2;else h=(r-g)/delta+4;h=(h*60+360)%360;}
    return [h,s,l];
  }
  function theme(config){
    const style=document.documentElement.style,[h,s]=hsl(config.primary.slice(1));
    for(const key of toneKeys){
      if(config.primary===defaults.primary){style.removeProperty('--ip-tone-'+key);continue;}
      const [,originalS,l]=hsl(key.slice(0,6));
      // Preserve light/dark roles; a light logo color must not turn white button text unreadable.
      const saturation=Math.min(.65,Math.max(.03,s)*Math.min(1,originalS/.23));
      const alpha=key.length===8?parseInt(key.slice(6),16)/255:1;
      style.setProperty('--ip-tone-'+key,`hsla(${h.toFixed(1)},${(saturation*100).toFixed(1)}%,${(l*100).toFixed(1)}%,${alpha})`);
    }
    style.setProperty('--ip-accent',config.accent);style.setProperty('--ip-primary',config.primary);
    document.documentElement.dataset.ipTheme=config.preset;
  }
  function avatar(config){
    const img=el('ipAvatar'),robot=el('robot'),version=++avatarVersion;
    img.hidden=true;robot.classList.remove('has-ip');
    const url=config.avatar_url||'';
    const allowed=url==='/aifeng.svg'||/^\/api\/appearance\/avatar\?revision=[a-f0-9]{32}$/.test(url)||(previewing&&url===uploaded&&url.startsWith('data:image/png;base64,'));
    img.onload=()=>{if(version!==avatarVersion)return;img.hidden=false;robot.classList.add('has-ip');};
    img.onerror=()=>{if(version!==avatarVersion)return;img.hidden=true;robot.classList.remove('has-ip');message('形象加载失败，已使用默认机器人。可重新上传PNG或选择内置形象。');};
    if(allowed){img.alt=config.name+'形象';img.src=url;}else img.removeAttribute('src');
  }
  function paint(config){
    theme(config);avatar(config);
    el('assistantBrandName').textContent=config.name||'小伴';
    el('conversationTitle').textContent='和'+(config.name||'小伴')+'聊聊';
    el('capture').textContent='拍一张，问'+(config.name||'小伴');
    document.title=(config.name||'小伴')+' · 陪伴助手';
  }
  function refreshLabels(){
    document.querySelectorAll('.message.assistant .message-name').forEach(node=>node.textContent=saved.name);
    if(typeof messages!=='undefined'&&!messages.length&&typeof render==='function')render();
    if(typeof wakeEnabled!=='undefined'&&!wakeEnabled)el('wakeHint').textContent='开启后说「'+saved.wake_word+'」，看到回应后再提问';
  }
  function apply(config){
    if(!config)return;
    saved={...defaults,...config,name:config.name?.trim()||'小伴'};
    if(!previewing)paint(saved);refreshLabels();
  }
  function message(text){el('ipStatus').textContent=text;}
  function active(){return typeof busy!=='undefined'&&(busy||recording||speaking||enrolling||wakeEnabled||wakeStarting||wakePhase==='command');}
  function controls(){
    const locked=working||active()||!info;
    for(const id of ['ipPreset','ipName','ipAvatarChoice','ipLogo','ipClearLogo','ipPrimary','ipAccent','ipWakeWord','ipPreview','ipSave'])el(id).disabled=locked;
    el('ipReload').disabled=working||active();
    el('ipLockHint').hidden=!active();
  }
  function fill(config){
    el('ipPreset').value=config.preset;el('ipName').value=config.name;
    el('ipAvatarChoice').value=config.avatar;el('ipPrimary').value=config.primary;el('ipAccent').value=config.accent;el('ipWakeWord').value=config.wake_word;
  }
  function draft(){return {preset:el('ipPreset').value,name:el('ipName').value.trim()||'小伴',avatar:el('ipAvatarChoice').value,primary:el('ipPrimary').value,accent:el('ipAccent').value,wake_word:el('ipWakeWord').value};}
  function previewUrl(config){
    if(config.avatar==='aifeng')return '/aifeng.svg';
    if(config.avatar==='custom')return uploaded||(clearLogo?'':saved.avatar_url?.startsWith('/api/appearance/avatar?')?saved.avatar_url:(info?.has_custom_logo?'/api/appearance/avatar?revision='+info.revision:''));
    return '';
  }
  async function load(){
    if(working||active())return;
    working=true;controls();
    try{
      const response=await fetch('/api/appearance');if(!response.ok)throw new Error('无法读取IP设置，请重试。');
      const next=await response.json();info=next;previewing=false;uploaded='';clearLogo=false;++uploadVersion;el('ipLogo').value='';
      apply(info.current);fill(saved);message(info.warning||`当前：${saved.name} · 唤醒词「${saved.wake_word}」。设置保存在本机。`);
    }catch(error){message(error.message);}finally{working=false;controls();}
  }
  el('ipPreset').onchange=()=>{const preset=presets[el('ipPreset').value];if(preset)fill(preset);message('尚未生效。可先预览，再保存名称、形象、主题和唤醒词。');};
  el('ipLogo').onchange=async()=>{
    if(active())return;const file=el('ipLogo').files?.[0];const version=++uploadVersion;if(!file)return;
    working=true;controls();
    try{
      if(file.type!=='image/png'||file.size>1_500_000)throw new Error('请选择不超过1.5 MB的PNG图片。');
      const raw=new Uint8Array(await file.arrayBuffer());
      if(version!==uploadVersion)return;
      if(raw.length<33||[137,80,78,71,13,10,26,10].some((v,i)=>raw[i]!==v))throw new Error('文件不是有效PNG图片。');
      const view=new DataView(raw.buffer),w=view.getUint32(16),h=view.getUint32(20);
      if(!w||!h||w>4096||h>4096)throw new Error('PNG边长须在1–4096像素之间。');
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('无法读取图片。'));reader.readAsDataURL(file);});
      if(version!==uploadVersion)return;uploaded=data;clearLogo=false;el('ipAvatarChoice').value='custom';el('ipPreset').value='custom';message('形象已读取，先预览，再保存。');
    }catch(error){uploaded='';el('ipLogo').value='';message(error.message);}finally{working=false;controls();}
  };
  el('ipClearLogo').onclick=()=>{if(active()||working)return;uploaded='';clearLogo=true;++uploadVersion;el('ipLogo').value='';el('ipAvatarChoice').value='default';message('准备移除上传形象；保存后生效。');};
  el('ipPreview').onclick=()=>{
    if(active()||working||!info)return;const config=draft();previewing=true;paint({...config,avatar_url:previewUrl(config)});
    message('仅预览外观，尚未保存；实际唤醒词仍为「'+saved.wake_word+'」。点击保存生效，或取消预览。');
  };
  el('ipReload').onclick=()=>load();
  el('ipSave').onclick=async()=>{
    if(active()||working||!info)return;
    working=true;controls();
    try{
      const body={revision:info.revision,current:draft(),clear_logo:clearLogo};if(uploaded)body.logo_base64=uploaded.split(',')[1];
      const response=await fetch('/api/appearance/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'IP设置保存失败。');
      info=result;previewing=false;uploaded='';clearLogo=false;el('ipLogo').value='';apply(info.current);fill(saved);
      message(info.warning||'已保存：'+saved.name+'；实际唤醒词已设为「'+saved.wake_word+'」，下次开启唤醒时使用。');
      if(typeof idle==='function')idle();
    }catch(error){previewing=false;paint(saved);message(error.message);}finally{working=false;controls();}
  };
  // The settings response is authoritative; a slow initial model-status reply
  // must not overwrite a newer saved IP configuration.
  return {apply,fromStatus:config=>{if(!info)apply(config);},load,controls,name:()=>saved.name,wakeWord:()=>saved.wake_word,sessionKeyword:word=>{if(['你好小伴','爱凤同学'].includes(word)){saved.wake_word=word;refreshLabels();}}};
})();
