import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

// Exercise the real API helpers and Axios serialization without a server or new test dependencies.
const { outputFiles } = await build({
  stdin: {
    contents: "export * from './services/api'; export { default as request } from './lib/request';",
    resolveDir: fileURLToPath(new URL('..', import.meta.url)),
    loader: 'ts',
  },
  bundle: true,
  platform: 'browser',
  format: 'esm',
  write: false,
});
const api = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);
globalThis.localStorage = { getItem: () => null, removeItem: () => {} };

let records;
let writes;
let failStatus;
let readResult;

beforeEach(() => {
  records = [
    { keyword: '价格', reply: '通用价格', item_id: '', type: 'text' },
    { keyword: '图片', reply: '', item_id: 'item-a', type: 'image', image_url: '/picture.png' },
    { keyword: '价格', reply: 'B价格', item_id: 'item-b', type: 'text' },
    { keyword: '价格', reply: 'A价格', item_id: 'item-a', type: 'text' },
  ];
  writes = [];
  failStatus = undefined;
  readResult = {};
  api.request.defaults.adapter = async config => {
    if (failStatus) {
      throw Object.assign(new Error('request failed'), { response: { status: failStatus } });
    }
    let data = readResult;
    if (config.method === 'get' && config.url.startsWith('/keywords-with-item-id/')) {
      data = structuredClone(records);
    } else if (config.method !== 'get') {
      const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
      writes.push({ method: config.method, url: config.url, body });
      if (config.url.startsWith('/keywords-with-item-id/')) {
        records = [...records.filter(item => item.type === 'image'), ...body.keywords];
      }
      data = { message: 'updated' };
    }
    return { data, status: 200, statusText: 'OK', headers: {}, config };
  };
});

test('adding a product keyword retains common and other product rules and images', async () => {
  const image = structuredClone(records[1]);
  await api.updateReplyRule({ keyword: '说明', reply_content: 'A说明', item_id: 'item-a' }, 'seller');
  assert.equal(writes[0].body.keywords.length, 4);
  assert.ok(writes[0].body.keywords.every(item => item.type !== 'image'));
  assert.equal(records.find(item => item.keyword === '说明').item_id, 'item-a');
  assert.equal(records.find(item => item.item_id === 'item-b').reply, 'B价格');
  assert.equal(records.find(item => !item.item_id).reply, '通用价格');
  assert.deepEqual(records.find(item => item.type === 'image'), image);
});

test('editing a filtered rule uses its identity even after the server reorders the list', async () => {
  const rule = (await api.getReplyRules('seller')).filter(item => item.item_id === 'item-a' && item.type === 'text')[0];
  records.reverse();
  await api.updateReplyRule({ ...rule, reply_content: 'A新价格' }, 'seller');
  assert.equal(records.find(item => item.item_id === 'item-a' && item.type === 'text').reply, 'A新价格');
  assert.equal(records.find(item => item.item_id === 'item-b').reply, 'B价格');
  assert.equal(records.find(item => !item.item_id).reply, '通用价格');
});

test('a keyword can move between product and common scopes', async () => {
  const rule = (await api.getReplyRules('seller')).find(item => item.item_id === 'item-b');
  await api.updateReplyRule({ ...rule, keyword: 'B专属说明', item_id: '' }, 'seller');
  assert.equal(records.find(item => item.keyword === 'B专属说明').item_id, '');
});

test('deleting a filtered rule keeps identical keywords for other products and common scope', async () => {
  const rule = (await api.getReplyRules('seller')).find(item => item.item_id === 'item-b');
  records.reverse();
  await api.deleteReplyRule(rule.id, 'seller');
  assert.equal(records.filter(item => item.keyword === '价格').length, 2);
  assert.ok(records.some(item => item.type === 'image'));
  assert.ok(records.some(item => !item.item_id));
  assert.ok(records.some(item => item.item_id === 'item-a' && item.type === 'text'));
});

test('a stale rule cannot accidentally edit or delete a different rule', async () => {
  const rule = (await api.getReplyRules('seller')).find(item => item.item_id === 'item-b');
  records = records.filter(item => item.item_id !== 'item-b');
  await assert.rejects(api.updateReplyRule({ ...rule, reply_content: 'changed' }, 'seller'), /已变更/);
  await assert.rejects(api.deleteReplyRule(rule.id, 'seller'), /已变更/);
  assert.equal(writes.length, 0);
});

test('image keywords remain read-only through the text replacement API', async () => {
  const rule = (await api.getReplyRules('seller')).find(item => item.type === 'image');
  assert.equal(rule.image_url, '/picture.png');
  await assert.rejects(api.updateReplyRule({ ...rule, reply_content: 'text' }, 'seller'), /图片关键词/);
  await assert.rejects(api.deleteReplyRule(rule.id, 'seller'), /图片关键词/);
  assert.equal(writes.length, 0);
});

test('only a 404 item reply is treated as unconfigured', async () => {
  failStatus = 404;
  assert.equal(await api.getItemReply('seller', 'item-a'), null);
  for (const status of [403, 500]) {
    failStatus = status;
    await assert.rejects(api.getItemReply('seller', 'item-a'), error => error.response.status === status);
  }
});

test('product default writes stay bound to the supplied account and product', async () => {
  await api.updateItemReply('seller a', 'item?b', '你好 {send_user_name}');
  await api.deleteItemReply('seller a', 'item?b');
  assert.deepEqual(writes, [
    { method: 'put', url: '/item-reply/seller%20a/item%3Fb', body: { reply_content: '你好 {send_user_name}' } },
    { method: 'delete', url: '/item-reply/seller%20a/item%3Fb', body: undefined },
  ]);
});

test('product and account item lists unwrap existing API responses', async () => {
  readResult = { items: [{ cookie_id: 'seller', item_id: 'item-a', reply_content: 'A' }] };
  assert.deepEqual(await api.getItemReplies('seller'), readResult.items);
  assert.deepEqual(await api.getAccountItems('seller'), readResult.items);
});
