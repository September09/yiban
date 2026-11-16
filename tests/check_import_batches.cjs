// 逻辑级检查：资料导入页必须自动显示已导入批次，失败提示必须贴在按钮旁。
// 用 DOM 桩模拟页面，不启动浏览器。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.textContent = '';
    this.className = ''; this.hidden = false; this.checked = false; this.disabled = false; this.value = '';
    this.type = ''; this.open = false; this.scrolled = 0;
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      toggle: (name, force) => { const on = force === undefined ? !classes.has(name) : !!force; on ? classes.add(name) : classes.delete(name); return on; },
      contains: name => classes.has(name),
    };
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

const html = read('static/index.html');
const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const calls = [];
let spaceVersion = 1;
let previewReply = null;

function json(body, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

const batches = [{ id: 'batch-1', name: '十一月活动纪要 · 第一批', actor_name: '胡红兵', created: '2026-11-14 20:00:00', visible_count: 3, active_count: 3, trash_count: 0, eligible_count: 3 }];

const context = vm.createContext({
  document: {
    getElementById: id => { assert.ok(ids[id], '缺少页面元素 #' + id); return ids[id]; },
    createElement: tag => new Element(tag),
    createTextNode: text => ({ textContent: text }),
    querySelectorAll: () => [],
    body: { className: '' },
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  window: { matchMedia: () => ({ matches: false }), addEventListener() {}, confirm: () => true },
  navigator: { mediaDevices: { getUserMedia: async () => { throw new Error('no device'); } } },
  fetch: async (requestPath, options) => {
    const data = options?.body ? JSON.parse(options.body) : null;
    calls.push({ path: requestPath, data });
    if (requestPath === '/api/shared/spaces/list') {
      return json({ account: { id: 'acc-1', name: '胡红兵' }, spaces: [{ id: 'space-1', name: '25A班', kind: 'team', role: 'owner', version: spaceVersion }] });
    }
    if (requestPath === '/api/shared/memories/list') {
      return json({ space: { id: 'space-1', name: '25A班', kind: 'team', role: 'owner', version: spaceVersion }, memories: [] });
    }
    if (requestPath === '/api/shared/members/list') {
      return json({ members: [{ id: 'acc-1', name: '胡红兵', role: 'owner' }], role: 'owner' });
    }
    if (requestPath === '/api/shared/batches/list') {
      return json({ batches, untracked_count: 0 });
    }
    if (requestPath === '/api/shared/batches/detail') {
      const identity = data?.batch_id || 'batch-1';
      return json({
        batch: { ...batches[0], id: identity, name: identity === 'batch-2' ? '新导入批次' : batches[0].name, can_undo: true, eligible_count: 3 },
        items: [{ id: 'm1', content: '破冰聚餐定在11月9日A班晚宴', status: 'pending', status_reason: '', event_date: '2026-11-09', source_name: '聚餐通知.pdf', source_note: '资料导入', author_name: '胡红兵', audience: ['胡红兵'], edit_policy: 'author', state: 'active', eligible: true, reason: '', has_evidence: false, version: 1 }],
        events: [],
      });
    }
    if (requestPath === '/api/shared/import/preview') {
      if (previewReply) return previewReply;
      return json({ error: '共同记忆需为 1–1200 个字。' }, 400);
    }
    if (requestPath === '/api/shared/import/confirm') {
      return json({ imported: 2, saved: true, batch_id: 'batch-2', batch_name: '新导入批次' });
    }
    return json({ ok: true });
  },
  TextDecoder, TextEncoder, AbortController, crypto: require('node:crypto').webcrypto,
  performance: { now: () => 0 }, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {}, console,
  URL, Blob, btoa: text => Buffer.from(text, 'binary').toString('base64'),
  $: id => { if (!ids[id]) throw new Error('缺少页面元素 #' + id); return ids[id]; },
  memoryTime: () => '2026-11-14 20:00',
  retrievalDiagnosticText: () => '',
  sharedOpenEvidence: null,
  sharedClearSourceCards: () => {},
  sharedVoiceActive: () => false,
});
context.globalThis = context;

async function waitFor(predicate, message) {
  for (let i = 0; i < 80 && !predicate(); i++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(predicate(), message);
}
const count = requestPath => calls.filter(call => call.path === requestPath).length;
// 桩元素不会把子节点文本拼进 textContent，这里自己拼。
const textOf = node => [node.textContent || '', ...(node.children || []).map(textOf)].join(' ');

(async () => {
  // 0. 提示必须先于操作按钮出现：表单很长，写在底部的错误用户看不到。
  const previewAt = html.indexOf('id="materialPreview"');
  const statusAt = html.indexOf('id="materialStatus"');
  const stopAt = html.indexOf('id="materialStop"');
  assert.ok(previewAt > 0 && statusAt > previewAt && statusAt < stopAt, '导入提示应紧跟在「查看最终导入预览」按钮之后');
  assert.ok(html.includes('id="materialConfirmStatus"'), '确认按钮旁也应有失败提示位');

  vm.runInContext(read('static/workspace.js'), context, { filename: 'static/workspace.js' });
  vm.runInContext(read('static/shared.js'), context, { filename: 'static/shared.js' });
  vm.runInContext(read('static/shared-batches.js'), context, { filename: 'static/shared-batches.js' });
  vm.runInContext(read('static/material-import.js'), context, { filename: 'static/material-import.js' });

  const space = { id: 'space-1', name: '25A班', kind: 'team', role: 'owner', version: 1 };
  vm.runInContext(`sharedState.token="tok";sharedState.account={id:"acc-1",name:"胡红兵"};sharedState.space=${JSON.stringify(space)};sharedState.members=[{id:"acc-1",name:"胡红兵",role:"owner"}];sharedState.memories=[];`, context);
  vm.runInContext('sharedRenderMemories();sharedControls();', context);

  // 1. 打开「资料导入」就自动读取批次，不需要先点刷新。
  const beforeOpen = count('/api/shared/batches/list');
  vm.runInContext('showSharedPage("import")', context);
  await waitFor(() => count('/api/shared/batches/list') > beforeOpen, '打开资料导入页后没有自动读取批次');
  await waitFor(() => ids['sharedBatchList'].children.length > 0, '批次列表没有渲染任何内容');
  const first = ids['sharedBatchList'].children[0];
  const firstText = textOf(first);
  assert.ok(firstText.includes('十一月活动纪要 · 第一批'), '已导入批次应直接显示：' + firstText);
  assert.ok(firstText.includes('当前可见 3 条'), '批次应显示可见条数：' + firstText);
  assert.ok(first.children.some(child => child.textContent === '查看本批记忆与来源'), '批次缺少查看入口');
  console.log('① 自动显示已导入批次：', firstText.slice(0, 60));

  // 2. 空间版本变化时不能把列表清空，应重新读取并保留内容。
  const beforeVersion = count('/api/shared/batches/list');
  spaceVersion = 3;
  await vm.runInContext('sharedLoadCurrent()', context);
  await waitFor(() => count('/api/shared/batches/list') > beforeVersion, '空间内容变化后没有重新读取批次');
  await waitFor(() => ids['sharedBatchList'].children.length > 0, '空间内容变化后批次列表被清空了');
  console.log('② 版本变化后批次仍在：', textOf(ids['sharedBatchList'].children[0]).slice(0, 60));

  // 3. 预览失败（400）时错误必须出现在按钮下方的提示里，并高亮。
  vm.runInContext('materialState.sources=[{name:"聚餐通知.pdf",text:"破冰聚餐定在11月9日A班晚宴",warnings:[]}];materialState.candidates=[{content:"破冰聚餐定在11月9日A班晚宴",selected:true,status:"pending",status_reason:"",source_name:"聚餐通知.pdf",source_note:"资料导入",evidence:"破冰聚餐定在11月9日A班晚宴",event_date:""}];materialRenderSources();materialRenderCandidates();materialControls();', context);
  assert.equal(ids['materialPreview'].disabled, false, '有勾选候选时预览按钮应可点');
  ids['materialPreview'].onclick();
  await waitFor(() => ids['materialStatus'].textContent.includes('1–1200'), '导入预览失败后没有在按钮旁显示原因');
  assert.ok(ids['materialStatus'].classList.contains('material-notice-error'), '导入失败提示应高亮显示');
  assert.ok(ids['materialStatus'].scrolled >= 1, '导入失败提示应滚动到可见位置');
  assert.ok(ids['materialStepHint'].textContent.includes('查看最终导入预览'), '步骤提示应说明下一步：' + ids['materialStepHint'].textContent);
  console.log('③ 失败提示：', ids['materialStatus'].textContent);

  // 4. 预览成功后确认导入：成功后自动刷新批次并展开本批。
  previewReply = json({
    token: 'plan-1', batch_name: '新导入批次', space_name: '25A班', actor_name: '胡红兵',
    items: [{ payload: { content: '破冰聚餐定在11月9日A班晚宴', event_date: '2026-11-09', source_note: '资料导入', status: 'pending', status_reason: '', visibility: 'all', edit_policy: 'author' }, audience: ['胡红兵'], evidence: null }],
    skipped: 0, conflicts: [],
  });
  ids['materialStatus'].classList.remove('material-notice-error');
  ids['materialPreview'].onclick();
  await waitFor(() => vm.runInContext('!!materialState.plan', context), '预览成功后没有生成导入计划');
  assert.equal(ids['materialReview'].hidden, false, '预览成功后应显示确认区');
  ids['materialConsent'].checked = true;
  vm.runInContext('materialControls()', context);
  assert.equal(ids['materialConfirm'].disabled, false, '勾选确认后应允许导入');
  const beforeConfirmBatches = count('/api/shared/batches/list');
  ids['materialConfirm'].onclick();
  await waitFor(() => count('/api/shared/import/confirm') > 0, '确认后没有发出导入请求');
  await waitFor(() => count('/api/shared/batches/detail') > 0, '导入成功后没有自动打开本批详情');
  await waitFor(() => ids['sharedBatchDetail'].hidden === false, '导入成功后批次详情没有展开');
  assert.equal(calls.find(call => call.path === '/api/shared/batches/detail').data.batch_id, 'batch-2', '应自动打开刚导入的批次');
  assert.ok(count('/api/shared/batches/list') > beforeConfirmBatches, '导入成功后没有刷新批次列表');
  assert.ok(ids['materialStatus'].textContent.includes('成功导入'), '导入成功后应给出成功提示：' + ids['materialStatus'].textContent);
  console.log('④ 导入成功后自动展开批次：', ids['sharedBatchInfo'].textContent.split('\n')[0]);
  console.log('IMPORT_BATCHES_OK');
})().catch(error => { console.error(error); process.exit(1); });
