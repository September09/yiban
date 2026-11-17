// 合并回归：主对话使用统一记忆范围，不能同时保留旧开关绕过「仅个人记忆」。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.textContent = '';
    this.className = ''; this.hidden = false; this.checked = false; this.disabled = false; this.value = '';
    this.type = ''; this.open = false; this.focused = false;
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      toggle: (name, force) => { const on = force === undefined ? !classes.has(name) : !!force; on ? classes.add(name) : classes.delete(name); return on; },
      contains: name => classes.has(name),
    };
  }
  scrollIntoView() {} focus() { this.focused = true; } click() { if (this.onclick) return this.onclick(); } remove() {}
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(k, v) { this[k] = v; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || new Element(); }
  querySelectorAll(sel) {
    const all = this.children.flatMap(child => [child, ...(child.querySelectorAll ? child.querySelectorAll('input') : [])]);
    if (sel.includes(':checked')) return all.filter(child => child.type === 'checkbox' && child.checked);
    if (sel === 'input') return all.filter(child => child.type === 'checkbox' || child.tagName === 'input');
    return all;
  }
  animate() { return { cancel() {} }; }
}

const html = read('static/index.html');
const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const context = vm.createContext({
  document: {
    getElementById: id => { assert.ok(ids[id], '缺少页面元素 #' + id); return ids[id]; },
    createElement: tag => new Element(tag), createTextNode: text => ({ textContent: text }),
    querySelectorAll: () => [], body: { className: '' },
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  window: { matchMedia: () => ({ matches: false }), addEventListener() {}, confirm: () => true },
  navigator: { mediaDevices: { getUserMedia: async () => { throw new Error('no device'); } } },
  fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  TextDecoder, TextEncoder, AbortController, crypto: require('node:crypto').webcrypto,
  performance: { now: () => 0 }, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {}, console,
  URL, Blob, btoa: text => Buffer.from(text, 'binary').toString('base64'),
  $: id => { if (!ids[id]) throw new Error('缺少页面元素 #' + id); return ids[id]; },
  memoryTime: () => '2026-11-14 20:00', retrievalDiagnosticText: () => '',
  sharedOpenEvidence: null, sharedClearSourceCards: () => {}, sharedVoiceActive: () => false,
});
context.globalThis = context;
vm.runInContext(read('static/shared.js'), context, { filename: 'static/shared.js' });

// Exercise the actual unified scope controls without a microphone or model.
vm.runInContext(`
  var busy=false,recording=false,enrolling=false,wakeStarting=false,wakePhase='idle',ready=true,ttsReady=true,activeProfile='profile-a';
  function cancelTurn(){} function controls(){} function render(){} function notice(){}
`,context);
vm.runInContext(read('static/workspace.js'),context,{filename:'static/workspace.js'});
vm.runInContext(read('static/companion-memory.js'),context,{filename:'static/companion-memory.js'});
const run=code=>vm.runInContext(code,context);
assert.equal(ids.chatSharedMemory,undefined,'旧开关不能与自动记忆入口并存');
assert.equal(run('typeof sharedChatScope'),'undefined','普通对话不能通过旧开关附带团队令牌');
assert.equal(run('companionMemory.mode'),'auto');
assert.equal(run('companionMemoryActive()'),false,'未登录空间账号时不读取团队资料');
assert.match(ids.chatMemoryHint.textContent,/空间登录可选/);
ids.chatMemoryManage.click();
assert.equal(run('workspacePage'),'shared');assert.equal(run('sharedPage'),'members');assert.equal(ids.sharedAccountName.focused,true);

run("sharedState.token='tok';sharedState.account={name:'测试账号'};sharedState.spaces=[{id:'team',name:'测试团队',version:1}];companionMemorySync();");
assert.equal(run('companionMemoryActive()'),true,'已登录后不需要先选空间');
assert.equal(ids.chatMemorySpace.hidden,true);
assert.match(ids.chatMemoryHint.textContent,/1个可访问空间/);
run("companionMemory.mode='personal';companionMemoryControls();");
assert.equal(run('companionMemoryActive()'),false,'仅个人模式不得引入团队资料');
assert.match(ids.chatMemoryHint.textContent,/只查询当前个人档案/);
run("companionMemory.mode='shared';companionMemoryControls();");
assert.equal(ids.chatMemorySpace.hidden,false);
assert.equal(ids.send.disabled,false,'未选择空间时可以普通聊天，不强制登录或选择');
run("sharedState.space={id:'team',name:'测试团队',version:1};companionMemoryControls();");
assert.equal(ids.send.disabled,false);
run("companionMemory.mode='auto';sharedState.token=null;sharedState.space=null;companionMemorySync();");
assert.equal(run('companionMemoryActive()'),false,'退出账号后不能继续访问团队');
assert.match(ids.chatMemoryHint.textContent,/空间登录可选/,'visitor hint must keep sign-in optional');
assert.equal(ids.send.disabled,false,'visitor can send ordinary text after logout');
assert.ok(!read('static/app.js').includes('sharedChatScope()'));
console.log('SHARED_CHAT_OK');
