import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, ref } from 'vue'
import * as queries from '../src/mobile/readQueries.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'
import { mobileParentLocation } from '../src/mobile/navigation.js'

function list(api, route = { path: '/m/inventory', fullPath: '/m/inventory', query: {} }, stored) {
  const code = parse(readFileSync(new URL('../src/mobile/MobileReadList.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const scrolls = [], calls = [], router = { replace: value => calls.push(value), push: value => calls.push(value), resolve: value => ({ ...value, fullPath: value.path }) }
  const dependencies = { ...queries, computed, ref, nextTick: async () => {}, onMounted() {}, onBeforeUnmount() {}, watch() {}, useRoute: () => route, useRouter: () => router, api, mobileStore: () => ({ list: () => stored, saveList: (_path, value) => { stored = value } }), returnToParent() {}, requestAnimationFrame: callback => callback(), document: { querySelectorAll: () => [{ id: 's3-material-4', getBoundingClientRect: () => ({ bottom: 120 }) }], getElementById: id => id === 's3-material-4' ? { getBoundingClientRect: () => ({ top: 200 }) } : null }, window: { scrollY: 600, scrollTo: value => scrolls.push(value) } }
  const state = new Function(...Object.keys(dependencies), code + '\nreturn {load,open,items,total,error,loading,criteria,change,refresh}')(...Object.values(dependencies))
  return { ...state, scrolls, calls, route }
}

test('remote queries preserve page past 100 and send only endpoint-supported criteria', () => {
  const criteria = queries.readCriteria({ view: 'locations', q: '同批号', page: '120', location_id: '107', material_id: '3', status: 'open' }, queries.inventoryViews, 'material')
  assert.deepEqual(queries.queryRequest(criteria, queries.inventoryViews), { endpoint: '/material-location-stock', params: { page: 120, page_size: 20, batch_no: '同批号', location_id: '107', material_id: '3' } })
  const receipts = queries.readCriteria({ view: 'receipts', q: 'MR-同号', status: 'reversed', page: '6' }, queries.recordViews, 'receipts')
  assert.deepEqual(queries.queryRequest(receipts, queries.recordViews).params, { page: 6, page_size: 20, q: 'MR-同号', status: 'reversed' })
  assert.equal(queries.readCriteria({ view: '__proto__', page: '-3', location_id: '../x' }, queries.inventoryViews, 'material').view, 'material')
  const stock = queries.readCriteria({ view: 'finished', product_id: '4', material_id: '3' }, queries.inventoryViews, 'material')
  assert.equal(stock.product_id, undefined)
  assert.equal(stock.material_id, undefined)
  assert.deepEqual(queries.queryRequest(stock, queries.inventoryViews).params, { page: 1, page_size: 20 })
})
test('all list configurations use existing read endpoints and repeated receipt movements have distinct anchors', () => {
  for (const [view, config] of Object.entries({ ...queries.inventoryViews, ...queries.recordViews })) {
    assert.match(config.endpoint, /^\/(material-|finished-|stock-documents\/reversals)/)
    assert.equal(queries.queryRequest({ view, page: 1, q: '' }, { [view]: config }).params.page_size, 20)
  }
  assert.notEqual(queries.rowKey('receipts', { id: 1, receipt_movement_id: 2 }), queries.rowKey('receipts', { id: 1, receipt_movement_id: 3 }))
  assert.equal(queries.quantityText('99999999999999.999999', '自定义'), '99999999999999.999999 自定义')
  assert.equal(queries.quantityText('-0.000001', 'kg'), '-0.000001 kg')
  assert.match(queries.scopeLabel(null, '__aggregate__'), /历史无批次/)
  assert.equal(queries.scopeLabel('batch', '真实001'), '真实批次：真实001')
})
test('detail parents preserve complete original criteria and reject external destinations', () => {
  const from = '/m/records?view=material&q=%E5%8D%95%E6%8D%AE&page=9'
  assert.equal(mobileParentLocation({ meta: { mobileReadDetail: true }, query: { from } }), from)
  for (const bad of ['https://evil.test/m/records', '//evil.test/m/records', '/orders', '/m/read/document/1', '/m/\\records']) assert.equal(queries.safeReadOrigin(bad), '/m/inventory')
  assert.equal(mobileLoginTarget('/m/read/receipt/105?from=%2Fm%2Fpending'), '/m/read/receipt/105?from=%2Fm%2Fpending')
  assert.equal(mobileLoginTarget('/m/read/document/0'), '/m')
})
test('async lists restore stored anchor after data succeeds and loading ends; failed reads never appear empty-successful', async () => {
  const state = list({ get: async () => ({ data: { items: [{ id: 4, remaining_quantity: '12.300000' }], total: 101 } }) }, undefined, { top: 12, anchor: 's3-material-4' })
  await state.load(true)
  assert.equal(state.items.value[0].remaining_quantity, '12.300000'); assert.equal(state.total.value, 101)
  assert.equal(state.loading.value, false); assert.deepEqual(state.scrolls, [{ top: 720 }])
  const fail = list({ get: async () => { throw new Error('服务不可用') } })
  await fail.load(true); assert.equal(fail.items.value.length, 0); assert.match(fail.error.value, /不可用/); assert.equal(fail.scrolls.length, 0)
})
test('late previous filter responses cannot overwrite latest list or errors', async () => {
  const pending = []
  const state = list({ get: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) })
  const old = state.load(true), recent = state.load(true)
  pending[1].resolve({ data: { items: [{ id: 2 }], total: 1 } }); await recent
  pending[0].reject(new Error('旧条件失败')); await old
  assert.equal(state.items.value[0].id, 2); assert.equal(state.error.value, ''); assert.equal(state.loading.value, false)
})
test('stale page moves to actual last server page, count detail links to exact document and batch', async () => {
  const route = { path: '/m/records', fullPath: '/m/records?view=counts&page=9', query: { view: 'counts', page: '9' } }
  const state = list({ get: async () => ({ data: { items: [], total: 21 } }) }, route)
  await state.load(true); assert.equal(state.calls[0].query.page, '2')
  state.open({ id: 7, document_id: 105, batch_id: 12 })
  assert.deepEqual(state.calls[1], { path: '/m/read/count/105', query: { from: route.fullPath, batch_id: '12' } })
})
test('read-only foreground refresh captures current list position before clearing async rows', async () => {
  let calls = 0
  const state = list({ get: async () => { calls++; return { data: { items: [{ id: 4 }], total: 1 } } } })
  await state.load(); await state.refresh()
  assert.equal(calls, 2); assert.deepEqual(state.scrolls, [{ top: 720 }])
})

function detail(api, route) {
  const code = parse(readFileSync(new URL('../src/mobile/MobileReadDetail.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const dependencies = { ...queries, computed, ref, onMounted() {}, onBeforeUnmount() {}, watch() {}, useRoute: () => route, api }
  return new Function(...Object.keys(dependencies), code + '\nreturn {load,detail,batches,error,loading,search,matchingBatches}')(...Object.values(dependencies))
}
test('zero-difference count locates exact document beyond 100 existing facts without new API', async () => {
  const calls = [], row = { id: 7, status: 'normal', transactions: [] }
  const state = detail({ async get(endpoint, options) {
    calls.push({ endpoint, options })
    if (endpoint === '/stock-documents/7') return { data: row }
    assert.equal(endpoint, '/material-counts')
    return { data: { total: 101, items: options.params.page === 1 ? Array.from({ length: 100 }, (_, id) => ({ document_id: id + 100 })) : [{ document_id: 7, book_quantity: '12.000001', actual_quantity: '12.000001', difference: '0.000000', unit: 'kg' }] } }
  } }, { params: { kind: 'count', id: '7' }, query: { batch_id: '3' } })
  await state.load(); assert.equal(calls.length, 3); assert.equal(calls[2].options.params.page, 2)
  assert.equal(state.detail.value.difference, '0.000000'); assert.deepEqual(state.detail.value.transactions, [])
})
test('finished batch search uses all server rows and failed reads clear prior authoritative values', async () => {
  let fail = false
  const state = detail({ async get(endpoint) {
    if (fail) throw new Error('查询失效')
    return { data: endpoint.endsWith('/batches') ? Array.from({ length: 105 }, (_, id) => ({ id, batch_no: 'FG-' + id, stock_scope: 'batch', quantity: '0.000001' })) : { id: 1, quantity: '1.000001', unit: '箱' } }
  } }, { params: { kind: 'finished', id: '1' }, query: {} })
  await state.load(); state.search.value = 'FG-104'; assert.equal(state.matchingBatches.value[0].id, 104)
  fail = true; await state.load(); assert.equal(state.detail.value, null); assert.equal(state.batches.value.length, 0); assert.match(state.error.value, /失效/)
})
