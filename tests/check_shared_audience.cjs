// 逻辑级检查：「指定成员」却一个都没选时，页面必须说清只有作者和管理员能看到。
// 用 DOM 桩模拟页面，不启动浏览器。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.textContent = '';
    this.className = ''; this.hidden = false; this.checked = false; this.disabled = false; this.value = '';
    this.type = ''; this.open = false;
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      toggle: (name, force) => { const on = force === undefined ? !classes.has(name) : !!force; on ? classes.add(name) : classes.delete(name); return on; },
      contains: name => classes.has(name),
    };
  }
  scrollIntoView() {} focus() {} click() { if (this.onclick) this.onclick(); } remove() {}
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
const textOf = node => [node.textContent || '', ...(node.children || []).map(textOf)].join(' ');
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

const space = { id: 'space-1', name: '25A班', kind: 'team', role: 'owner', version: 4 };
vm.runInContext(`sharedState.token="tok";sharedState.account={id:"acc-1",name:"胡红兵"};sharedState.space=${JSON.stringify(space)};
sharedState.members=[{id:"acc-1",name:"胡红兵",role:"owner"},{id:"acc-2",name:"张宁宁",role:"editor"},{id:"acc-3",name:"王烨",role:"reader"}];`, context);
ids['sharedVisibility'].value = 'selected';

const memory = (id, content, visibility, viewers) => ({
  id, content, visibility, viewers, edit_policy: 'author', event_date: '', source_note: '', status: 'valid', status_reason: '已确认',
  author: 'acc-1', author_name: '胡红兵', editor_name: '胡红兵', updated: '2026-11-14 13:14:38', version: 1,
  has_evidence: false, can_edit: true, can_configure: true, can_delete: true, can_history: true,
});
vm.runInContext(`sharedState.memories=${JSON.stringify([
  memory('m1', '25A班的口号是风华正茂、A班骄傲', 'selected', []),
  memory('m2', '生日会定在12月20日', 'selected', ['acc-2']),
  memory('m3', '集合地点是学校东门', 'all', []),
])}`, context);

// 1. 默认「指定成员」且没选人：编辑器必须警告只有作者和管理员可见。
vm.runInContext('sharedState.edit=null;sharedRenderAudience();', context);
const emptyHint = ids['sharedAudienceHint'].textContent;
assert.ok(emptyHint.includes('0 位成员'), '未选成员时必须说明选了 0 位：' + emptyHint);
assert.ok(emptyHint.includes('只有你（作者）和空间管理员能看到'), '未选成员时必须说清实际可见范围：' + emptyHint);
assert.ok(ids['sharedAudienceHint'].classList.contains('audience-warning'), '警告应高亮显示');
console.log('① 未选成员的提示：', emptyHint);

// 2. 勾选一位成员后提示改为已选人数。
const boxes = ids['sharedAudience'].querySelectorAll('input');
const normal = boxes.filter(box => box.dataset.implicit !== 'true');
assert.equal(normal.length, 2, '应只有两位非默认可见成员');
normal[0].checked = true;
vm.runInContext('sharedRenderAudienceHint();', context);
assert.ok(ids['sharedAudienceHint'].textContent.includes('已选 1 位成员'), '勾选后应显示已选人数');
assert.ok(!ids['sharedAudienceHint'].classList.contains('audience-warning'), '正常选择不应再高亮警告');
console.log('② 选中一位后：', ids['sharedAudienceHint'].textContent);

// 3. 选择全体成员时提示直接说明范围。
ids['sharedVisibility'].value = 'all';
vm.runInContext('sharedRenderAudienceHint();', context);
assert.ok(ids['sharedAudienceHint'].textContent.includes('全体成员可见'), '全体成员应明确说明：' + ids['sharedAudienceHint'].textContent);
console.log('③ 全体成员：', ids['sharedAudienceHint'].textContent);

// 4. 记忆列表要暴露「指定成员但没人可见」，并给出一键改法。
ids['sharedVisibility'].value = 'selected';
vm.runInContext('sharedRenderMemories();', context);
const rows = ids['sharedMemoryList'].children;
assert.equal(rows.length, 3, '应渲染三条记忆');
const m1 = textOf(rows[0]);
assert.ok(m1.includes('仅作者和管理员（未选择其他成员'), '列表必须标明没人可见：' + m1);
assert.ok(m1.includes('改为全体成员可见'), '应提供一键改为全体成员可见');
assert.ok(textOf(rows[1]).includes('指定成员（1 人）'), '有指定成员时应显示人数：' + textOf(rows[1]));
assert.ok(textOf(rows[2]).includes('全体成员'), '全体成员记忆应显示全体成员');
console.log('④ 列表可见范围：', m1.split('\n').find(line => line.startsWith('可见')) || m1.slice(0, 80));

// 5. 一键改法只装进编辑区、不写入：可见范围改成全体，提示需预览确认。
const share = rows[0].children.find(child => child.textContent === '改为全体成员可见');
assert.ok(share, '缺少一键改法按钮');
share.click();
assert.equal(ids['sharedVisibility'].value, 'all', '一键改法应把可见范围设为全体成员');
assert.equal(ids['sharedMemoryText'].value, '25A班的口号是风华正茂、A班骄傲', '应同时载入这条记忆');
assert.ok(ids['sharedEditorStatus'].textContent.includes('保存修改'), '应提示仍需预览确认：' + ids['sharedEditorStatus'].textContent);
console.log('⑤ 一键改法：', ids['sharedEditorStatus'].textContent);
console.log('SHARED_AUDIENCE_OK');
