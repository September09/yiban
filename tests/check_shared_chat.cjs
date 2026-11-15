// 逻辑级检查：陪伴对话的「带上当前空间共同记忆」开关必须默认关闭、未登录不可用、勾选才附带令牌。
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

const box = ids['chatSharedMemory'], hint = ids['chatSharedMemoryHint'];
assert.ok(box, '陪伴对话缺少「带上当前空间共同记忆」开关');

// 1. 未登录：开关不可用、未勾选，提示说明为什么。
vm.runInContext('sharedChatMemorySync();', context);
assert.equal(box.disabled, true, '未登录时开关必须禁用');
assert.equal(box.checked, false, '未登录时不能默认带上共同记忆');
assert.ok(hint.textContent.includes('未登录空间账号'), '未登录必须说明原因：' + hint.textContent);
assert.equal(vm.runInContext('sharedChatScope()', context), null, '未登录时不能把任何空间令牌带进对话');
console.log('① 未登录：', hint.textContent);

// 1.5 登录了但没选空间：这是最常见的“没法选”，必须点名缺哪一步，并给出一键入口。
const go = ids['chatSharedMemoryGo'];
assert.ok(go, '对话页应提供“去登录/去选择空间”的一键入口');
assert.equal(go.hidden, false, '未登录时应显示入口');
assert.ok(go.textContent.includes('登录'), '未登录时入口应指向登录：' + go.textContent);
vm.runInContext("sharedState.token='tok-123';sharedState.account={name:'25A班'};sharedState.spaces=[{id:'space-1',name:'25A班',kind:'team',role:'owner',version:3}];sharedChatMemorySync();", context);
assert.equal(box.disabled, true, '只登录不选空间时不能勾选（没有空间就无从检索）');
assert.ok(hint.textContent.includes('没有选择空间'), '应说明是“没选空间”而不是“没登录”：' + hint.textContent);
assert.ok(go.textContent.includes('选择空间'), '入口文案应改成去选择空间：' + go.textContent);
console.log('①.5 已登录未选空间：', hint.textContent);

// 1.6 从对话页点入口：必须切到共同记忆页并把焦点放到空间下拉。
vm.runInContext('showWorkspacePage=()=>{};showSharedPage=()=>{};sharedChatMemoryGo();', context);
assert.equal(ids['sharedSpaceSelect'].focused, true, '应把焦点放到空间下拉');
vm.runInContext('sharedState.token=null;sharedState.account=null;sharedState.spaces=[];sharedChatMemoryGo();', context);
assert.equal(ids['sharedAccountName'].focused, true, '未登录时应把焦点放到账号输入框');

// 2. 登录并选择空间：开关可用，提示写清只带可见范围与云端限制。
const space = { id: 'space-1', name: '25A班', kind: 'team', role: 'reader', version: 3 };
vm.runInContext(`sharedState.token='tok-123';sharedState.space=${JSON.stringify(space)};`, context);
vm.runInContext('sharedChatMemorySync();', context);
assert.equal(box.disabled, false, '登录并选择空间后开关应可用');
assert.ok(hint.textContent.includes('25A班'), '提示应写出空间名：' + hint.textContent);
assert.ok(hint.textContent.includes('只带入你在这个空间可见'), '必须说明只带可见范围：' + hint.textContent);
assert.ok(hint.textContent.includes('云端模型仅使用全体成员可见'), '必须说明云端模型的限制：' + hint.textContent);
console.log('② 已登录：', hint.textContent);

// 3. 默认不勾选：请求里不带令牌；勾选后才带。
assert.equal(box.checked, false, '默认必须关闭');
assert.equal(vm.runInContext('sharedChatScope()', context), null);
box.checked = true;
const scope = vm.runInContext('sharedChatScope()', context);
assert.equal(scope.shared_token, 'tok-123');
assert.equal(scope.shared_space_id, 'space-1');
console.log('③ 勾选后的请求字段：', JSON.stringify(scope));

// 4. 切换空间：勾选必须撤销，避免把上一个空间的资料带进新空间。
box.checked = true;
vm.runInContext(`sharedState.space=${JSON.stringify({ id: 'space-2', name: '另一个团队', kind: 'team', role: 'editor', version: 1 })};sharedChatMemorySync();`, context);
assert.equal(box.checked, false, '切换空间后必须重新确认，不能沿用上一个空间的勾选');
assert.ok(hint.textContent.includes('另一个团队'), '提示应跟随当前空间：' + hint.textContent);
console.log('④ 切换空间后提示：', hint.textContent);

// 5. 退出登录：勾选被撤销，不会残留令牌。
vm.runInContext('sharedState.token=null;sharedState.space=null;sharedChatMemorySync();', context);
assert.equal(box.checked, false, '退出登录后必须撤销勾选');
assert.equal(box.disabled, true, '退出登录后开关应禁用');
assert.equal(vm.runInContext('sharedChatScope()', context), null);

// 5. 页面文案与对话请求都要跟上：不能还写着“不会读取共同记忆”。
const note = html.slice(html.indexOf('访客不读取任何个人记忆'), html.indexOf('访客不读取任何个人记忆') + 260);
assert.ok(note.includes('带上当前空间共同记忆'), '个人记忆页说明应指向对话页开关：' + note);
assert.ok(!note.includes('不会读取家庭／团队共同记忆'), '说明不应再断言完全不读取共同记忆');
const app = read('static/app.js');
assert.ok(app.includes("sharedChatScope()"), '对话请求必须通过 sharedChatScope() 组装共同记忆参数');
assert.ok(read('static/shared.js').includes('shared_token'), '请求字段 shared_token 应由 sharedChatScope 提供');
assert.ok(app.includes('data.shared_memory'), '页面应显示本轮带入了多少条共同记忆');
console.log('⑥ 文案与请求已对接');

console.log('SHARED_CHAT_OK');
