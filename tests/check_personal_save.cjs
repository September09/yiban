// 保存声纹/保存记忆必须点得动、且给出明确结果，不能静默返回。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.textContent = ''; this.className = ''; this.hidden = false; this.checked = false; this.disabled = false; this.value = ''; this.classList = { toggle() {}, remove() {} }; }
  scrollIntoView() {} focus() {} click() {} remove() {} append(...c) { this.children.push(...c); } replaceChildren() { this.children = []; }
  setAttribute(k, v) { this[k] = v; } querySelector() { return new Element(); } querySelectorAll() { return []; }
}

const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const calls = [];
const PROFILES = [{ id: 'p1', name: '胡红兵', salutation: '胡同学', enrolled: 1 }];
const context = vm.createContext({
  document: { getElementById: id => ids[id], createElement: () => new Element(), createTextNode: t => ({ textContent: t }), querySelectorAll: () => [], body: { className: '' } },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  window: { matchMedia: () => ({ matches: false }), addEventListener() {}, confirm: () => true },
  navigator: { mediaDevices: { getUserMedia: async () => { throw new Error('no device'); } } },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  console, URL, Blob, btoa: s => Buffer.from(s, 'binary').toString('base64'),
  setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  crypto: require('node:crypto').webcrypto, TextDecoder, TextEncoder, Float32Array, Uint8Array, AbortController,
  Audio: class { async play() {} pause() {} },
  $: id => ids[id],
  api: async (url, body) => {
    calls.push({ url, body });
    if (url === '/api/profiles/list') return { json: async () => ({ profiles: PROFILES }) };
    if (url === '/api/memory/list') return { json: async () => ({ memories: [], conflicts: [] }) };
    if (url === '/api/memory/save') return { json: async () => ({ id: 'm1' }) };
    if (url === '/api/speaker/enroll') return { json: async () => ({ enrolled: true }) };
    return { json: async () => ({}) };
  },
  notice: text => { ids.notice.textContent = text; },
  state() {}, controls() {}, cancelTurn() {}, pauseWake: async () => {}, resumeWake: async () => {},
  render() {}, readChatHistory: () => [], busy: false, recording: false, wakePhase: '', wakeEnabled: false,
  speakerReady: true, activeProfile: '', messages: [], memoryBusy: false, enrolling: false,
});
vm.runInContext(fs.readFileSync(path.join(ROOT, 'static/personal.js'), 'utf8'), context);

const run = code => vm.runInContext(code, context);
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  await tick();
  assert.equal(run('profiles.length'), 1, '档案列表应加载');

  // 1) 访客状态下点保存记忆：必须说明原因，不能静默。
  run("$('memoryText').value='我喜欢安静一点'");
  ids.notice.textContent = '';
  await run("$('saveMemory').onclick()");
  assert.match(ids.notice.textContent, /选择或创建用户档案/, '访客保存记忆应提示先选档案');

  // 2) 选中档案后保存记忆：给出成功与状态提示。
  run("activeProfile='p1'");
  ids.notice.textContent = '';
  await run("$('saveMemory').onclick()");
  await tick();
  const saved = calls.find(c => c.url === '/api/memory/save');
  assert.ok(saved, '应调用保存接口');
  assert.match(ids.notice.textContent, /记忆已保存/, '保存记忆应提示成功');
  assert.match(ids.notice.textContent, /待核实/, '应说明新记忆的状态');

  // 3) 未同意就点保存声纹：提示勾选同意，而不是没反应。
  run("enrollmentSamples=[];$('voiceConsent').checked=false");
  ids.notice.textContent = '';
  await run("$('saveVoice').onclick()");
  assert.match(ids.notice.textContent, /同意/, '未同意应提示勾选同意');

  // 4) 段数不足：说明还差几段。
  run("enrollmentSamples=['a','b'];$('voiceConsent').checked=true");
  ids.notice.textContent = '';
  await run("$('saveVoice').onclick()");
  assert.match(ids.notice.textContent, /还差|还需要录制 1 段/, '段数不足应提示还差一段');

  // 5) 三段齐全：调用接口并提示已保存。
  run("enrollmentSamples=['a','b','c']");
  ids.notice.textContent = '';
  await run("$('saveVoice').onclick()");
  await tick();
  const enroll = calls.find(c => c.url === '/api/speaker/enroll');
  assert.ok(enroll && enroll.body.recordings.length === 3 && enroll.body.consent === true, '应提交三段录音并带同意');
  assert.match(ids.notice.textContent, /声纹已保存/, '保存声纹应提示成功');

  // Recording failures must be visible beside the button even with chat hidden.
  assert.match(html, /id="enrollmentStatus"[^>]*role="status"/);
  let starts=0, stops=0, allow;
  context.micError=e=>e.message;
  context.Microphone=class {async start(){starts++;await new Promise(resolve=>{allow=resolve;});}async stop(){stops++;}};
  run("activeProfile='';enrollmentSamples=[];$('voiceConsent').checked=true");
  await run("recordEnrollment()");
  assert.match(ids.enrollmentStatus.textContent,/当前用户/);
  assert.equal(starts,0,'Guest must not open microphone');
  run("activeProfile='p1';$('voiceConsent').checked=true");
  const pending=run("recordEnrollment()");await tick();
  assert.match(ids.enrollmentStatus.textContent,/权限提示/);
  assert.equal(ids.recordVoice.disabled,true,'Cannot start a second pending capture');
  await run("recordEnrollment()");assert.equal(starts,1);
  await run("cancelEnrollment()");allow();await pending;
  assert.equal(run('enrollmentSamples.length'),0,'Late permission cannot add a recording');
  assert.ok(stops>0);
  context.Microphone=class {async start(){throw new Error('麦克风权限被拒绝');}async stop(){}};
  run("$('voiceConsent').checked=true");
  await run("recordEnrollment()");
  assert.match(ids.enrollmentStatus.textContent,/未开始录音.*权限被拒绝/);
  assert.equal(run('enrolling'),false);
  assert.equal(run('enrollmentStarting'),false);
  assert.equal(ids.recordVoice.disabled,false);

  // 6) 打开自动识别时，说明文字不参与识别，避免误以为声纹失效。
  ids.notice.textContent = '';
  run("$('speakerAuto').checked=true");
  await run("$('speakerAuto').onchange()");
  await tick();
  assert.match(ids.notice.textContent, /自动匹配|文字输入/, '开启自动识别应有说明');

  console.log('Personal save checks passed: save actions always report a visible result.');
})().catch(error => { console.error(error); process.exit(1); });
