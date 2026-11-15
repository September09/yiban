// 逻辑级检查：共同记忆「编辑 → 保存」入口是否可见、可达、语义清楚。
// 用 DOM 桩模拟页面，不启动浏览器；断言与真实页面的按钮文案保持一致。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.textContent = '';
    this.className = ''; this.hidden = false; this.checked = false; this.disabled = false; this.value = '';
    this.type = ''; this.open = false; this.classList = { toggle() {}, remove() {}, add() {} };
    this.scrolled = 0;
  }
  scrollIntoView() { this.scrolled++; }
  focus() { this.focused = true; }
  click() { if (this.onclick) this.onclick(); }
  remove() {}
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

const ids = Object.fromEntries([...read('static/index.html').matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const calls = [];
let previewPlan = null;

const context = vm.createContext({
  document: {
    getElementById: id => ids[id],
    createElement: tag => new Element(tag),
    createTextNode: text => ({ textContent: text }),
    querySelectorAll: () => [],
    body: { className: '' },
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  window: { matchMedia: () => ({ matches: false }), addEventListener() {}, confirm: () => true },
  navigator: { mediaDevices: { getUserMedia: async () => { throw new Error('no device'); } } },
  fetch: async (requestPath, options) => {
    calls.push({ path: requestPath, data: options?.body ? JSON.parse(options.body) : null });
    if (requestPath === '/api/shared/spaces/list') {
      return { ok: true, status: 200, json: async () => ({ account: { id: 'acc-1', name: '胡红兵' }, spaces: [{ id: 'space-1', name: '胡家', kind: 'family', role: 'owner', version: 1 }] }) };
    }
    if (requestPath === '/api/shared/memories/preview') {
      return { ok: true, status: 200, json: async () => previewPlan };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  },
  TextDecoder, TextEncoder, AbortController, crypto: require('node:crypto').webcrypto,
  performance: { now: () => 0 }, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {}, console,
  URL, Blob, btoa: text => Buffer.from(text, 'binary').toString('base64'),
  $: id => { if (!ids[id]) throw new Error('缺少页面元素 #' + id); return ids[id]; },
  memoryTime: () => '2026-11-14 20:00',
  retrievalDiagnosticText: () => '',
  sharedOpenEvidence: null,
});
context.globalThis = context;

vm.runInContext(read('static/workspace.js'), context, { filename: 'static/workspace.js' });
vm.runInContext(read('static/shared.js'), context, { filename: 'static/shared.js' });

const space = { id: 'space-1', name: '胡家', kind: 'family', role: 'owner', version: 1 };
const item = {
  id: 'mem-1', content: '十月一日上午九点小区门口集合', status: 'pending', status_reason: '',
  event_date: '2026-10-01', source_note: '家庭讨论', visibility: 'selected', edit_policy: 'author',
  viewers: [], author: 'acc-1', author_name: '胡红兵', editor_name: '胡红兵',
  updated: '2026-11-14T12:00:00Z', version: 3, can_edit: true, can_delete: true, can_history: true,
  has_evidence: false, can_correct_replacement: false,
};

function findButton(row, text) {
  return row.children.find(child => child.textContent === text);
}
async function waitFor(predicate, message) {
  for (let i = 0; i < 60 && !predicate(); i++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(predicate(), message);
}

(async () => {
  vm.runInContext(`sharedState.token="tok";sharedState.account={id:"acc-1",name:"胡红兵"};sharedState.space=${JSON.stringify(space)};sharedState.members=[{id:"acc-1",name:"胡红兵",role:"owner"}];sharedState.memories=${JSON.stringify([item])};`, context);
  vm.runInContext('sharedRenderMemories();sharedControls();', context);

  // 1. 列表里必须能找到「编辑共同记忆」，否则用户无从保存修改。
  const row = ids['sharedMemoryList'].children[0];
  const editButton = findButton(row, '编辑共同记忆');
  assert.ok(editButton, '记忆列表缺少「编辑共同记忆」按钮：' + row.children.map(child => child.textContent).join(' | '));

  // 2. 点击编辑后表单回填，且主按钮文案必须直接说明「保存」。
  editButton.onclick();
  assert.equal(ids['sharedEditorTitle'].textContent, '编辑共同记忆');
  assert.equal(ids['sharedMemoryText'].value, item.content);
  assert.equal(ids['sharedEditorDisclosure'].open, true, '编辑时表单应自动展开');
  assert.ok(ids['sharedEditorDisclosure'].scrolled >= 1, '编辑时应把表单滚动到可见位置');
  assert.ok(ids['sharedPreview'].disabled === false, '编辑态下保存按钮应可点击');
  assert.ok(ids['sharedPreview'].textContent.includes('保存'), '编辑态按钮应写明保存，实际：' + ids['sharedPreview'].textContent);
  assert.ok(ids['sharedSaveHint'].textContent.includes('勾选确认'), '应提示保存分两步：' + ids['sharedSaveHint'].textContent);
  assert.ok(ids['sharedStatus'].textContent.includes('保存修改'), '点击编辑后应提示保存入口：' + ids['sharedStatus'].textContent);
  console.log('① 编辑入口可见：', ids['sharedPreview'].textContent, '|', ids['sharedSaveHint'].textContent);

  // 3. 状态选了「有效」却没写核实依据时，本地就要拦住并说明原因，不能白发一次注定 400 的请求。
  const before = calls.filter(call => call.path === '/api/shared/memories/preview').length;
  ids['sharedMemoryText'].value = item.content;
  ids['sharedMemoryStatus'].value = 'valid';
  ids['sharedStatusReason'].value = '';
  ids['sharedPreview'].onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.filter(call => call.path === '/api/shared/memories/preview').length, before, '缺少核实依据时不应发出预览请求');
  assert.ok(ids['sharedEditorStatus'].hidden === false, '缺少核实依据时应在按钮旁给出提示');
  assert.ok(ids['sharedEditorStatus'].textContent.includes('核实依据'), '提示应说明缺什么：' + ids['sharedEditorStatus'].textContent);
  assert.equal(ids['sharedReviewPanel'].hidden, true, '缺少核实依据时不应出现核对面板');
  console.log('③ 本地拦截：', ids['sharedEditorStatus'].textContent);

  // 4. 修改内容后点保存（预览），核对区要出现并给出下一步。
  ids['sharedMemoryStatus'].value = 'pending';
  ids['sharedMemoryText'].value = '十月一日上午八点半小区门口集合';
  previewPlan = {
    token: 'plan-1', kind: 'family', space_name: '胡家', author_name: '胡红兵', actor_name: '胡红兵',
    before: { content: item.content, has_evidence: false },
    after: { content: ids['sharedMemoryText'].value, status: 'pending', status_reason: '', visibility: 'selected', edit_policy: 'author' },
    audience: ['胡红兵'], conflicts: [],
  };
  ids['sharedPreview'].onclick();
  await waitFor(() => !!vm.runInContext('sharedState.plan', context), '点击保存后没有生成核对方案');
  assert.equal(ids['sharedReviewPanel'].hidden, false, '保存后应显示核对面板');
  assert.ok(ids['sharedReviewPanel'].scrolled >= 1, '核对面板应滚动到可见位置');
  assert.equal(ids['sharedPublishConsent'].disabled, false, '核对勾选框应可勾选');
  assert.equal(ids['sharedConfirm'].disabled, true, '未勾选确认前不应允许写入');
  assert.ok(ids['sharedConfirm'].textContent.includes('保存修改'), '编辑态的确认按钮应写明保存修改：' + ids['sharedConfirm'].textContent);
  assert.ok(ids['sharedReviewContent'].children.some(child => (child.textContent || '').includes('下一步')), '核对区应说明下一步，实际：' + ids['sharedReviewContent'].children.map(child => child.textContent).join(' | '));
  console.log('② 核对面板：勾选框可用，确认按钮禁用；下一步提示已显示');

  // 4. 勾选后才能确认，确认后应发出保存请求。
  ids['sharedPublishConsent'].checked = true;
  ids['sharedPublishConsent'].onchange();
  assert.equal(ids['sharedConfirm'].disabled, false, '勾选确认后应允许保存');
  ids['sharedConfirm'].onclick();
  await waitFor(() => calls.some(call => call.path === '/api/shared/memories/confirm'), '确认后没有发出保存请求');
  const confirmCall = calls.find(call => call.path === '/api/shared/memories/confirm');
  assert.deepEqual(confirmCall.data, { space_id: 'space-1', token: 'plan-1', confirmed: true, acknowledge_conflicts: false });
  console.log('④ 保存请求：', JSON.stringify(confirmCall.data));

  // 5. 取消编辑后回到新建态，文案也要跟着切换，避免误以为是修改。
  vm.runInContext('sharedResetEditor();sharedControls();', context);
  assert.equal(ids['sharedPreview'].textContent, '保存新记忆（先预览核对）');
  assert.equal(ids['sharedConfirm'].textContent, '确认发布共同记忆');
  console.log('⑤ 新建态文案：', ids['sharedPreview'].textContent);
  console.log('SHARED_EDIT_SAVE_OK');
})().catch(error => { console.error(error); process.exit(1); });
