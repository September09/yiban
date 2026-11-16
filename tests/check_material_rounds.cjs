// 大文件导入：分轮规则必须在把资料送给 organize 前保证每轮 ≤10 份且合计 ≤30000 字。
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');

// 只提取分轮纯函数，避免拉起整页 DOM 桩。
const source = read('static/material-import.js');
const start = source.indexOf('function materialOrganizeRounds');
const end = source.indexOf('}', source.indexOf('return rounds;')) + 1;
assert.ok(start > 0 && end > start, 'material-import.js 应包含 materialOrganizeRounds 纯函数');
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context, { filename: 'materialOrganizeRounds' });
const rounds = vm.runInContext('materialOrganizeRounds', context);

// 1. 单份小资料：一轮。
let result = rounds([{ name: 'a', text: 'x'.repeat(100) }]);
assert.equal(result.length, 1);
assert.equal(result[0].length, 1);
console.log('① 单份资料：1 轮');

// 2. 模拟大 JSON 自动分块后的 8 份 × 12000 字：合计 96000 → 按 30000 字分轮。
result = rounds(Array.from({ length: 8 }, (_, i) => ({ name: 'part' + i, text: 'x'.repeat(12000) })));
assert.equal(result.length, 4, '96000 字应分成 4 轮：' + result.length);
assert.ok(result.every(batch => batch.reduce((n, s) => n + s.text.length, 0) <= 30000), '每轮合计不得超过 30000 字');
assert.ok(result.every(batch => batch.length <= 10), '每轮不得超过 10 份');
console.log('② 8 块 × 12000 字：', result.length, '轮，各轮字数', result.map(b => b.reduce((n, s) => n + s.text.length, 0)).join('/'));

// 3. 10 份小资料的份数上限：第 11 份进入下一轮。
result = rounds(Array.from({ length: 11 }, (_, i) => ({ name: 's' + i, text: 'x' })));
assert.equal(result.length, 2);
assert.equal(result[0].length, 10);
assert.equal(result[1].length, 1);
console.log('③ 份数上限：10 + 1 两轮');

// 4. 单份超过 30000 字必须拒绝，并指出资料名（提示用户重新读取自动分块）。
assert.throws(() => rounds([{ name: '聊天记录.json', text: 'x'.repeat(30001) }]),
  error => error.message.includes('聊天记录.json'), '应指出是哪份资料超限');
console.log('④ 超限资料：', (() => { try { rounds([{ name: '聊天记录.json', text: 'x'.repeat(30001) }]); } catch (e) { return e.message; } })());

// 5. 空列表：不产生轮次（按钮本身禁用，双保险）。
assert.equal(rounds([]).length, 0);
console.log('⑤ 空列表：0 轮');

console.log('MATERIAL_ROUNDS_OK');
