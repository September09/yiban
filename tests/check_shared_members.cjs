// 逻辑级检查：团队空间「增加成员」必须说得清前置条件，失败原因要显示在按钮旁。
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
const textOf = node => [node.textContent || '', ...(node.children || []).map(textOf)].join(' ');

const space = { id: 'space-1', name: '25A班', kind: 'team', role: 'owner', version: 4 };
let members = [{ id: 'acc-1', name: '25A班', role: 'owner' }];
let memberError = null;
const calls = [];

const reply = (body, ok = true, status = 200) => ({ ok, status, json: async () => body });
const fetch = async (url, options = {}) => {
  const route = String(url).replace('/api/shared', '');
  const data = options.body ? JSON.parse(options.body) : {};
  calls.push({ route, data });
  if (route === '/spaces/list') return reply({ account: { id: 'acc-1', name: '25A班' }, spaces: [space] });
  if (route === '/memories/list') return reply({ space, memories: [] });
  if (route === '/members/list') return reply({ members, role: space.role });
  if (route === '/auth/register') {
    if (data.name === '张宁宁' || data.name === '王烨') return reply({ error: '这个空间账号名称已被使用。' }, false, 400);
    return reply({ registered: true });
  }
  if (route === '/members/set') {
    if (memberError) return reply({ error: memberError }, false, 400);
    if (!members.some(m => m.name === data.name)) members = [...members, { id: 'acc-9', name: data.name, role: data.role }];
    return reply({ updated: true });
  }
  return reply({});
};

const context = vm.createContext({
  document: {
    getElementById: id => { assert.ok(ids[id], '缺少页面元素 #' + id); return ids[id]; },
    createElement: tag => new Element(tag), createTextNode: text => ({ textContent: text }),
    querySelectorAll: () => [], body: { className: '' },
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  window: { matchMedia: () => ({ matches: false }), addEventListener() {}, confirm: () => true },
  navigator: { mediaDevices: { getUserMedia: async () => { throw new Error('no device'); } } },
  fetch, TextDecoder, TextEncoder, AbortController, crypto: require('node:crypto').webcrypto,
  performance: { now: () => 0 }, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {}, console,
  URL, Blob, btoa: text => Buffer.from(text, 'binary').toString('base64'),
  $: id => { if (!ids[id]) throw new Error('缺少页面元素 #' + id); return ids[id]; },
  memoryTime: () => '2026-11-14 20:00', retrievalDiagnosticText: () => '',
  sharedOpenEvidence: null, sharedClearSourceCards: () => {}, sharedVoiceActive: () => false,
});
context.globalThis = context;
vm.runInContext(read('static/shared.js'), context, { filename: 'static/shared.js' });

vm.runInContext(`sharedState.token="tok";sharedState.account={id:"acc-1",name:"25A班"};sharedState.space=${JSON.stringify(space)};`, context);
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

(async () => {
  // 1. 管理员只有自己一人：必须说清「先注册账号」，并给出空名单原因。
  vm.runInContext('sharedState.members=[{id:"acc-1",name:"25A班",role:"owner"}];sharedRenderMembers();', context);
  const hint = ids['sharedMemberHint'].textContent;
  assert.ok(hint.includes('1 位成员'), '应显示当前成员数：' + hint);
  assert.ok(hint.includes('在这里为他注册'), '必须提示对方先注册账号：' + hint);
  assert.ok(hint.includes('上限 100'), '应说明成员上限：' + hint);
  assert.ok(textOf(ids['sharedMemberList']).includes('还没有其他成员'), '空名单要说明原因：' + textOf(ids['sharedMemberList']));
  console.log('① 管理员提示：', hint);
  console.log('   空名单：', textOf(ids['sharedMemberList']));

  // 2. 名称为空：不发请求，就地指出问题。
  ids['sharedMemberStatus'].hidden = true;
  ids['sharedMemberName'].value = '   ';
  calls.length = 0;
  ids['sharedSetMember'].click();
  await settle();
  assert.equal(calls.filter(c => c.route === '/members/set').length, 0, '空名称不应发请求');
  assert.ok(ids['sharedMemberStatus'].textContent.includes('请先填写'), '空名称必须就地提示：' + ids['sharedMemberStatus'].textContent);
  assert.equal(ids['sharedMemberStatus'].hidden, false, '提示必须可见');
  assert.ok(ids['sharedMemberName'].focused, '应把焦点放回名称输入框');
  console.log('② 空名称：', ids['sharedMemberStatus'].textContent);

  // 3. 账号不存在：错误要显示在按钮旁，并指向注册入口。
  memberError = '该空间账号不存在，请先让成员注册账号。';
  ids['sharedMemberName'].value = '张宁宁';
  ids['sharedMemberRole'].value = 'reader';
  ids['sharedSetMember'].click();
  await settle();
  const missing = ids['sharedMemberStatus'].textContent;
  assert.ok(missing.includes('没有找到空间账号「张宁宁」'), '应指出找不到哪个账号：' + missing);
  assert.ok(missing.includes('在这里为他注册'), '应告诉用户去哪里注册：' + missing);
  assert.ok(ids['sharedMemberStatus'].classList.contains('material-notice-error'), '失败提示应高亮');
  console.log('③ 账号不存在：', missing);

  // 4. 添加成功：说明角色、更新后的成员数，并提醒还要在记忆里勾选。
  memberError = null;
  calls.length = 0;
  ids['sharedSetMember'].click();
  await settle(); await settle();
  const added = calls.find(c => c.route === '/members/set');
  assert.ok(added, '应发出添加请求');
  assert.equal(added.data.name, '张宁宁');
  assert.equal(added.data.role, 'reader');
  const done = ids['sharedMemberStatus'].textContent;
  assert.ok(done.includes('已把「张宁宁」设为只读成员'), '成功提示应说明角色：' + done);
  assert.ok(done.includes('2 位成员'), '成功提示应显示更新后的成员数：' + done);
  assert.ok(done.includes('「指定成员」'), '必须提醒还要勾选指定可见：' + done);
  assert.equal(ids['sharedMemberName'].value, '', '成功后应清空名称输入框');
  assert.ok(ids['sharedMemberHint'].textContent.includes('2 位成员'), '名单提示应同步更新：' + ids['sharedMemberHint'].textContent);
  assert.ok(textOf(ids['sharedMemberList']).includes('张宁宁 · 只读成员'), '名单应出现新成员');
  console.log('④ 添加成功：', done);
  console.log('   名单：', textOf(ids['sharedMemberList']).slice(0, 60));

  // 5. 非管理员：不给表单，但要说清为什么、当前是什么角色。
  vm.runInContext(`sharedState.space=${JSON.stringify({ ...space, role: 'reader' })};sharedRenderMembers();`, context);
  const readerHint = ids['sharedMemberHint'].textContent;
  assert.ok(readerHint.includes('只有空间管理员可以添加或移除成员'), '非管理员必须说明权限：' + readerHint);
  assert.ok(readerHint.includes('只读成员'), '应说明当前角色：' + readerHint);
  console.log('⑤ 非管理员：', readerHint);

  // 6. 登录状态下为对方注册：口令太短不发请求，成功后填好名称、不改当前登录。
  vm.runInContext(`sharedState.space=${JSON.stringify(space)};sharedRenderMembers();`, context);
  calls.length = 0;
  ids['sharedNewMemberName'].value = '王烨';
  ids['sharedNewMemberPassword'].value = 'short';
  ids['sharedRegisterMember'].click();
  await settle();
  assert.equal(calls.filter(c => c.route === '/auth/register').length, 0, '口令太短不应发请求');
  assert.ok(ids['sharedRegisterMemberStatus'].textContent.includes('10–128'), '口令太短必须就地提示：' + ids['sharedRegisterMemberStatus'].textContent);
  console.log('⑥ 口令太短：', ids['sharedRegisterMemberStatus'].textContent);

  // 7. 名称重复：说明可直接按该名称添加成员。
  ids['sharedNewMemberPassword'].value = 'pass12345678';
  ids['sharedRegisterMember'].click();
  await settle();
  const duplicate = ids['sharedRegisterMemberStatus'].textContent;
  assert.ok(duplicate.includes('已被使用'), '重名必须提示：' + duplicate);
  assert.ok(duplicate.includes('按该名称添加'), '重名时应提示可直接添加：' + duplicate);
  console.log('⑦ 重名：', duplicate);

  // 8. 注册成功：名称带到添加框，口令清空，当前登录不变。
  ids['sharedNewMemberName'].value = '王烨二';
  ids['sharedRegisterMember'].click();
  await settle();
  const registered = ids['sharedRegisterMemberStatus'].textContent;
  assert.ok(registered.includes('已注册账号「王烨二」'), '应确认注册成功：' + registered);
  assert.ok(registered.includes('当前登录没有被切换'), '必须说明登录未被切换：' + registered);
  assert.equal(ids['sharedMemberName'].value, '王烨二', '应把名称带到添加框');
  assert.equal(ids['sharedNewMemberPassword'].value, '', '注册成功后应清空口令');
  assert.equal(vm.runInContext('sharedState.account.name', context), '25A班', '注册不应切换当前账号');
  console.log('⑧ 注册成功：', registered);

  console.log('SHARED_MEMBERS_OK');
})().catch(error => { console.error(error); process.exit(1); });
