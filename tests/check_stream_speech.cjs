// 检查「回答边生成边朗读」：每句要真的播放，不能在播放开始时被自己停掉。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(ROOT, name), 'utf8');
const html = read('static/index.html'), appJs = read('static/app.js'), diagJs = read('static/conversation-diagnostics.js');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.style = {}; this.textContent = ''; this.className = ''; this.hidden = false; this.checked = false; this.disabled = false; this.value = ''; this.classList = { toggle() {}, remove() {} }; }
  scrollIntoView() {} focus() {} click() {} remove() {} append(...c) { this.children.push(...c); } replaceChildren() { this.children = []; }
  setAttribute(k, v) { this[k] = v; } querySelector() { return new Element(); } querySelectorAll() { return []; }
  animate() { return { cancel() {} }; }
}

const CHAT_LINES = [
  { message: { role: 'assistant', content: '今天我们先把这一段话完整地说完，再自然地继续下一句话。' }, done: false },
  { message: { role: 'assistant', content: '这一句话会在前一句播放的时候提前合成，保持内容顺序不变。' }, done: false },
  { message: { role: 'assistant', content: '最后半句' }, done: true },
];

function build({ failAt = 0, lines = CHAT_LINES } = {}) {
  const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
  const speakCalls = [], audioInstances = [];
  let speakCount = 0, chatDone = false;
  const context = vm.createContext({
    document: { getElementById: id => ids[id], createElement: () => new Element(), createTextNode: t => ({ textContent: t }), querySelectorAll: () => [], body: { className: '' } },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    window: { matchMedia: () => ({ matches: false }), addEventListener() {} },
    navigator: { mediaDevices: { getUserMedia: async () => { throw new Error('no device'); } } },
    fetch: async (url, options) => {
      if (url === '/api/status') return { json: async () => ({ chat: true, asr: true, wake: true, vision: true, tts: true, appearance: {} }) };
      if (url === '/api/diagnostics/trace') return { ok: true, json: async () => ({ steps: [], available: true }) };
      if (url === '/api/turn/cancel') return { ok: true, json: async () => ({ cancelled: true }) };
      if (url === '/api/audio/devices') return { ok: true, json: async () => ({ available: false, devices: [], message: '' }) };
      if (url === '/api/speak') {
        speakCalls.push(JSON.parse(options.body).text); speakCount++;
        if (failAt && speakCount === failAt) return { ok: false, json: async () => ({ error: '合成服务不可用' }) };
        return { ok: true, blob: async () => new Blob(['x']) };
      }
      if (url === '/api/chat') {
        return {
          ok: true, body: {
            getReader: () => ({
              read: async () => {
                if (chatDone) return { done: true };
                chatDone = true;
                return { done: false, value: new TextEncoder().encode(lines.map(l => JSON.stringify(l)).join('\n') + '\n') };
              }, cancel() {}
            })
          }
        };
      }
      throw new Error('unexpected ' + url);
    },
    TextDecoder, TextEncoder, Float32Array, Uint8Array, ArrayBuffer, DataView, AbortController,
    crypto: require('node:crypto').webcrypto, URL, Blob, btoa: s => Buffer.from(s, 'binary').toString('base64'),
    performance: { now: () => 0 }, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}, console,
    Audio: class {
      constructor() { this.paused = true; this.ended = false; this.pauseCalls = 0; this.src = ''; audioInstances.push(this); }
      async play() { this.paused = false; }
      pause() { this.paused = true; if (!this.ended) this.pauseCalls++; }
    },
  });
  vm.runInContext(diagJs, context);
  vm.runInContext(appJs, context);
  return { context, ids, speakCalls, audioInstances };
}

const tick = () => new Promise(resolve => setImmediate(resolve));

async function runHappyPath() {
  const { context, speakCalls, audioInstances } = build();
  await tick();
  vm.runInContext("ttsReady=true;$('autoplay').checked=true;wakeEnabled=false;", context);
  const sending = vm.runInContext("send('说点别的')", context);
  for (let i = 0; i < 20 && !audioInstances.length; i++) await tick();
  // 第一句一到就开始合成，不必等整段回答结束。
  assert.ok(speakCalls.length >= 1, '第一句应当在回答生成完之前就开始合成');
  assert.equal(speakCalls[0], '今天我们先把这一段话完整地说完，再自然地继续下一句话。');
  let played = 0;
  for (let guard = 0; guard < 60 && played < 3; guard++) {
    if (audioInstances.length > played) {
      const player = audioInstances[played];
      for (let i = 0; i < 20 && !player.onended; i++) await tick();
      assert.equal(player.pauseCalls, 0, `第 ${played + 1} 段在播完前被暂停了，会导致自动播放没有声音`);
      assert.equal(player.paused, false, `第 ${played + 1} 段没有真正进入播放状态`);
      played++;
      player.ended = true; player.paused = true;
      await player.onended();
    }
    await tick();
  }
  assert.equal(played, 3, '三段回答都应各播放一次');
  await sending;
  assert.deepEqual(speakCalls, ['今天我们先把这一段话完整地说完，再自然地继续下一句话。', '这一句话会在前一句播放的时候提前合成，保持内容顺序不变。', '最后半句']);
}

async function runFailurePath() {
  const { context, ids, speakCalls, audioInstances } = build({ failAt: 2 });
  await tick();
  vm.runInContext("ttsReady=true;$('autoplay').checked=true;wakeEnabled=false;", context);
  let done = false;
  const sending = vm.runInContext("send('说点别的')", context).then(() => { done = true; });
  for (let i = 0; i < 20 && !audioInstances.length; i++) await tick();
  const player = audioInstances[0];
  for (let i = 0; i < 20 && player && !player.onended; i++) await tick();
  if (player) { player.ended = true; player.paused = true; await player.onended(); }
  // 第二段合成失败时这一轮必须收尾，否则会一直卡在「正在说」、麦克风也不会恢复。
  for (let i = 0; i < 200 && !done; i++) await tick();
  assert.ok(done, '合成失败后本轮没有收尾，会一直卡住');
  assert.equal(audioInstances.length, 1, '合成失败后不应继续播放');
  // 预取可能已经把后一句也发了出去，这里只关心第一段真的播过。
  assert.equal(speakCalls[0], '今天我们先把这一段话完整地说完，再自然地继续下一句话。');
  assert.ok(ids.notice.textContent.includes('合成服务不可用'), '应提示合成失败：' + ids.notice.textContent);
}

async function runBatchedClauses() {
  const text = '这次合计1,234本，资料已经核对。接下来可以继续聊。';
  const { context, speakCalls, audioInstances } = build({lines:[{message:{content:text},done:true}]});
  await tick();
  vm.runInContext("ttsReady=true;$('autoplay').checked=true;wakeEnabled=false;", context);
  let done=false;
  const sending=vm.runInContext("send('说点别的')",context).then(()=>{done=true;});
  let played=0;
  for(let guard=0;guard<200&&!done;guard++) {
    const player=audioInstances[played];
    if(player?.onended) {played++;player.ended=true;player.paused=true;await player.onended();}
    await tick();
  }
  assert.ok(done,'整段到达时也应按短句播放并正常结束');
  await sending;
  assert.equal(speakCalls[0],text,'完整短回答不应拆碎，数字也必须保持完整');
  assert.equal(speakCalls.join(''),text,'分段必须保持完整原文顺序');
  assert.equal(played,speakCalls.length,'每段只播放一次');
}

async function runLateSentencePrefetch() {
  const {context,speakCalls,audioInstances}=build();
  await tick();
  vm.runInContext('wakeEnabled=false;',context);
  const speech=vm.runInContext('openReplySpeech(turnVersion,null)',context);
  speech.feed('根据资料，');
  await tick();assert.equal(speakCalls.length,0,'过短的逗号前缀不应独立发声');
  speech.feed('今天我们先把这一段话完整地说完，再自然地继续下一句话。');
  for(let i=0;i<40&&!audioInstances[0]?.onended;i++)await tick();
  assert.equal(speakCalls[0],'今天我们先把这一段话完整地说完，再自然地继续下一句话。');
  speech.feed('今天我们先把这一段话完整地说完，再自然地继续下一句话。这一句话会在前一句播放的时候提前合成，保持内容顺序不变。');
  for(let i=0;i<40&&speakCalls.length<2;i++)await tick();
  assert.equal(speakCalls.length,2,'后到的句子应在前句播完之前预取');
  assert.equal(audioInstances[0].ended,false);
  const done=speech.end('今天我们先把这一段话完整地说完，再自然地继续下一句话。这一句话会在前一句播放的时候提前合成，保持内容顺序不变。');
  audioInstances[0].ended=true;await audioInstances[0].onended();
  for(let i=0;i<40&&!audioInstances[1]?.onended;i++)await tick();
  audioInstances[1].ended=true;await audioInstances[1].onended();
  await done;
  assert.deepEqual(speakCalls,['今天我们先把这一段话完整地说完，再自然地继续下一句话。','这一句话会在前一句播放的时候提前合成，保持内容顺序不变。']);
}

(async () => {
  await runHappyPath();
  await runFailurePath();
  await runBatchedClauses();
  await runLateSentencePrefetch();
  console.log('Stream speech checks passed: sentences play in order, nothing is stopped early, failures still settle.');
})().catch(error => { console.error(error); process.exit(1); });
