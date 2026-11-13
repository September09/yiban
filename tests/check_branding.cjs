const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(){this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.files=[];this.classes=new Set();this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x)};}
 removeAttribute(name){delete this[name];}
}
const ids={};for(const match of fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g))ids[match[1]]=new Element();
const properties={},root={style:{setProperty:(k,v)=>properties[k]=v,removeProperty:k=>delete properties[k]},dataset:{}};
const label=new Element(),base={preset:'default',name:'小伴',primary:'#356b56',accent:'#97b48b',avatar:'default',wake_word:'你好小伴',avatar_url:''};
const phoenix={preset:'aifeng',name:'爱凤同学',primary:'#71529d',accent:'#d9a441',avatar:'aifeng',wake_word:'爱凤同学',avatar_url:'/aifeng.svg'};
let current={...base},revision='a'.repeat(32),calls=[],failSave=false,capture=0,playback=0;
const context=vm.createContext({document:{getElementById:id=>ids[id],documentElement:root,querySelectorAll:()=>[label]},busy:false,recording:false,speaking:false,enrolling:false,wakeEnabled:false,wakeStarting:false,wakePhase:'',console,
 navigator:{mediaDevices:{getUserMedia:()=>capture++}},Audio:class{play(){playback++;}},
 fetch:async(path,options)=>{calls.push(path);if(options){const body=JSON.parse(options.body);if(failSave)return{ok:false,json:async()=>({error:'先关闭语音唤醒'})};assert.equal(body.revision,revision);current={...body.current,avatar_url:body.current.avatar==='aifeng'?'/aifeng.svg':''};revision='b'.repeat(32);}
 return{ok:true,json:async()=>({current:{...current},revision,has_custom_logo:false})};}});
vm.runInContext(fs.readFileSync('static/branding.js','utf8'),context);
const call=code=>vm.runInContext(code,context);
(async()=>{
 assert.equal(calls.length,0);await call('branding.load()');assert.equal(ids.assistantBrandName.textContent,'小伴');assert.equal(ids.ipAvatar.hidden,true);
 ids.ipPreset.value='aifeng';ids.ipPreset.onchange();ids.ipPreview.onclick();
 assert.equal(calls.length,1,'preview must not persist or start listening');
 assert.match(ids.ipStatus.textContent,/实际唤醒词仍为「你好小伴」/);assert.equal(call('branding.wakeWord()'),'你好小伴');
 assert.equal(ids.assistantBrandName.textContent,'爱凤同学');assert.equal(ids.ipAvatar.src,'/aifeng.svg');ids.ipAvatar.onload();assert.ok(ids.robot.classes.has('has-ip'));
 assert.ok(properties['--ip-tone-243e38'].startsWith('hsla('));assert.equal(properties['--ip-accent'],'#d9a441');
 ids.ipAvatar.onerror();assert.ok(!ids.robot.classes.has('has-ip'));assert.match(ids.ipStatus.textContent,/默认机器人/);
 await ids.ipReload.onclick();assert.equal(ids.assistantBrandName.textContent,'小伴');assert.equal(properties['--ip-tone-243e38'],undefined);
 ids.ipPreset.value='aifeng';ids.ipPreset.onchange();await ids.ipSave.onclick();
 assert.equal(call('branding.name()'),'爱凤同学');assert.equal(call('branding.wakeWord()'),'爱凤同学');assert.equal(label.textContent,'爱凤同学');
 context.oldConfig=base;call('branding.fromStatus(oldConfig)');assert.equal(call('branding.name()'),'爱凤同学','late model status cannot revert saved IP');
 assert.match(ids.wakeHint.textContent,/爱凤同学/);assert.equal(ids.conversationTitle.textContent,'和爱凤同学聊聊');
 ids.ipName.value='';ids.ipAvatarChoice.value='custom';await ids.ipSave.onclick();assert.equal(call('branding.name()'),'小伴');assert.equal(ids.ipAvatar.hidden,true);
 call('wakeEnabled=true;branding.controls()');assert.equal(ids.ipSave.disabled,true);assert.equal(ids.ipLockHint.hidden,false);
 const before=calls.length;await ids.ipSave.onclick();ids.ipPreview.onclick();assert.equal(calls.length,before);
 call('wakeEnabled=false;branding.controls()');ids.ipName.value='尚未保存';ids.ipPreview.onclick();failSave=true;await ids.ipSave.onclick();assert.equal(ids.assistantBrandName.textContent,'小伴');assert.match(ids.ipStatus.textContent,/关闭语音/);
 context.config={...phoenix,avatar_url:'https://example.com/tracking.png'};call('branding.apply(config)');assert.equal(ids.ipAvatar.src,undefined);assert.ok(!ids.robot.classes.has('has-ip'));
 call('branding.sessionKeyword("爱凤同学")');assert.equal(call('branding.wakeWord()'),'爱凤同学');
 assert.equal(capture,0);assert.equal(playback,0);
 console.log('IP settings: preview/save, default fallback, palette, avatar failure, busy guard and no audio passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
