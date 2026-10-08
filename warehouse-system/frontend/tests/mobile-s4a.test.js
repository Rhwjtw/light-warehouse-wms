import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { createTaskStore, lockedTask, taskLabels } from '../src/mobile/taskState.js'
import * as receipt from '../src/mobile/receipt.js'
import { mobileParentLocation, completedTaskLocation, returnToParent } from '../src/mobile/navigation.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'
import { readGate } from '../src/mobile/readQueries.js'

const fields = { materialId: '107', supplierId: '111', unit: 'kg', batchNo: '同批号', quantity: '12.300001', productionDate: '2026-10-07', expiryDate: '2026-12-01', notes: '备注' }
function setup() {
  const values = new Map(), storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  let seq = 0
  const config = { storage, instance: 'isolated', user: 9, newId: () => `request-key-${++seq}` }
  const store = createTaskStore(config), task = store.create(receipt.receiptKind, 'new', '/m/records?view=receipts&q=同号&page=6')
  store.edit(task.id, fields)
  return { store, task, values, config }
}
const permit = { ready: true, allowed: true }
function success(task) { return { request_id: task.requestId, id: 500, document_no: 'RM-500', kind: 'material_receipt', batch_id: 700, quantity: task.snapshot.quantity, unit: 'kg', after_quantity: '999', token: 'discard' } }
function detail(task) { return { id: 500, batch_id: 700, receipt_movement_id: 900, material_id: 107, supplier_id: 111, batch_no: task.snapshot.batchNo, production_date: fields.productionDate, expiry_date: fields.expiryDate, unit: 'kg', received_quantity: task.snapshot.quantity, pending_quantity: '12.300001' } }

test('receipt business allowlist retains full notes, excludes credentials and authoritative balance across reload', () => {
  const { store, task, values, config } = setup()
  store.edit(task.id, { ...fields, notes: '备'.repeat(2000), password: 'do-not-save', token: 'do-not-save', pending_quantity: '88888' })
  const restored = createTaskStore(config)
  assert.equal(restored.get(task.id).fields.notes.length, 2000)
  assert.equal(restored.get(task.id).fields.supplierId, '111')
  assert.ok(![...values.values()].join('').includes('do-not-save'))
  assert.ok(![...values.values()].join('').includes('88888'))
})
test('receipt exact decimal normalization, malformed dates and scope payload never silently round', () => {
  assert.equal(receipt.receiptSnapshot({ ...fields, quantity: '12.300000' }).quantity, '12.3')
  for (const quantity of ['0', '-1', '0.0000001', '1e2', '100000000000000']) assert.throws(() => receipt.receiptSnapshot({ ...fields, quantity }))
  assert.throws(() => receipt.receiptSnapshot({ ...fields, productionDate: '2026-02-30' }))
  assert.throws(() => receipt.receiptSnapshot({ ...fields, expiryDate: '2026-01-01' }))
  const { store, task } = setup(), started = store.begin(task.id, permit)
  assert.deepEqual(Object.keys(receipt.receiptPayload(started)).sort(), ['request_id', 'material_id', 'supplier_id', 'batch_no', 'quantity', 'unit', 'production_date', 'expiry_date', 'notes'].sort())
  assert.equal(typeof receipt.receiptPayload(started).quantity, 'string')
})
test('receipt explicit rejection keeps key; changed content uses new key; unknown cannot begin or create replacement', () => {
  const { store, task, config } = setup(), first = store.begin(task.id, permit)
  store.settle(task.id, 'rejected', 'date')
  assert.equal(store.begin(task.id, permit).requestId, first.requestId)
  store.settle(task.id, 'rejected', 'date'); store.edit(task.id, { ...fields, batchNo: '另一批' })
  const next = store.begin(task.id, permit); assert.notEqual(next.requestId, first.requestId)
  store.suspend()
  const restored = createTaskStore(config)
  assert.equal(restored.get(task.id).status, 'uncertain')
  assert.throws(() => restored.begin(task.id, permit))
  assert.equal(restored.create(receipt.receiptKind, 'new', '/m/jobs').id, task.id)
  assert.equal(restored.settle(task.id, 'rejected'), false)
})
test('success binds request/document/batch then exact movement, persists identity only, history closes old form', () => {
  const { store, task, config } = setup(), started = store.begin(task.id, permit)
  assert.throws(() => store.settle(task.id, 'succeeded', '', { ...success(started), request_id: 'other' }))
  assert.equal(store.get(task.id).status, 'submitting')
  store.settle(task.id, 'succeeded', '', success(started))
  store.bindReceipt(task.id, detail(started))
  const done = createTaskStore(config).get(task.id)
  assert.deepEqual(done.result, { id: 500, batchId: 700, documentNo: 'RM-500', movementId: 900 })
  assert.equal(done.fields.quantity, '')
  assert.throws(() => store.bindReceipt(task.id, { ...detail(started), receipt_movement_id: 901 }))
  assert.throws(() => store.bindReceipt(task.id, { ...detail(started), supplier_id: 110 }))
  assert.deepEqual(completedTaskLocation({ params: { step: 'form' } }, done), { path: receipt.receiptTaskPath(done, 'result'), replace: true })
  assert.equal(mobileParentLocation({ meta: { mobileReceiptTask: true }, params: { step: 'result' }, query: {} }, done), receipt.receiptOrigin(done.origin))
  assert.notEqual(store.create(receipt.receiptKind, 'new', '/m/jobs').id, task.id)
})
test('existing summary GET pages beyond 100 exact matching IDs, receipt deep links remain local', async () => {
  const calls = [], api = { async get(path, { params }) { calls.push({ path, params }); return { data: { total: 101, items: params.page === 1 ? [{ id: 1, remaining_quantity: '100' }] : [{ id: 107, remaining_quantity: '0.000001' }] } } } }
  assert.equal((await receipt.readReceiptStock(api, { id: 107, code: 'E2E' })).remaining_quantity, '0.000001')
  assert.equal(calls.length, 2); assert.equal(calls[1].params.page, 2)
  assert.ok(!Object.hasOwn(calls[0].params, 'material_id'))
  assert.equal(receipt.receiptOrigin('//evil.test/m/records'), '/m/jobs')
  assert.equal(mobileLoginTarget('/m/jobs/receipt/request-key-1/result'), '/m/jobs/receipt/request-key-1/result')
})

function component(api, setupState = setup()) {
  const { store, task } = setupState
  const route = { params: { jobId: task.id, step: 'confirm' }, path: receipt.receiptTaskPath(task, 'confirm'), fullPath: receipt.receiptTaskPath(task, 'confirm'), query: {} }
  const calls = [], router = { replace: async x => { calls.push(x); route.params.step = 'result' }, push: async x => calls.push(x), options: { history: { state: {} } } }
  const auth = { user: { id: 9, role: 'admin' } }
  const code = parse(readFileSync(new URL('../src/mobile/MobileReceipt.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const deps = { ...receipt, computed, reactive, ref, nextTick: async () => {}, onMounted() {}, onBeforeUnmount() {}, watch() {}, useRoute: () => route, useRouter: () => router, api, auth, canOperate: () => true, mobileRevision: ref(0), mobileStore: () => store, touchMobile() {}, lockedTask, taskLabels, readGate, returnToParent }
  deps.touchMobile = () => { deps.mobileRevision.value++ }
  return { ...new Function(...Object.keys(deps), code + '\nloadFields(); return {submit,confirm,refreshResult,form,error,detail,stock,resume,restore,material,supplier}')(...Object.values(deps)), store, task, route, calls, touch: deps.touchMobile }
}
function realReads(path) {
  if (path === '/auth/me') return { id: 9, role: 'admin' }
  if (path === '/materials/107') return { id: 107, code: 'E2E', name: '原料', unit: 'kg', is_active: true }
  if (path === '/suppliers/111') return { id: 111, name: '供应商', is_active: true }
}
test('actual SFC synchronously prevents double click during auth reads and POST; result read failure leaves success', async () => {
  let release, posts = 0
  const api = { async get(path) { if (realReads(path)) return { data: realReads(path) }; throw new Error('refresh failed') }, async post(_path, payload) { posts++; await new Promise(r => release = r); return { data: { ...success({ requestId: payload.request_id, snapshot: fields }), quantity: payload.quantity } } } }
  const state = component(api), pending = state.submit()
  await state.submit(); await new Promise(r => setTimeout(r, 0))
  assert.equal(posts, 1); release(); await pending
  assert.equal(state.store.get(state.task.id).status, 'succeeded')
  assert.match(state.error.value, /已成功.*待刷新/)
  assert.equal(state.detail.value, null); await state.submit(); assert.equal(posts, 1)
})
test('actual SFC response loss, conflict, 503 and unauthorized remain locked; resume only GET; 422 may modify', async () => {
  for (const status of [undefined, 409, 503, 401, 422, 403]) {
    let posts = 0
    const api = { async get(path) { return { data: realReads(path) } }, async post() { posts++; throw Object.assign(new Error('lost/rejected'), { status }) } }
    const state = component(api); await state.submit()
    assert.equal(state.store.get(state.task.id).status, [422, 403].includes(status) ? 'rejected' : 'uncertain')
    state.resume(); await new Promise(r => setTimeout(r, 0)); assert.equal(posts, 1)
    if (![422, 403].includes(status)) { await state.submit(); assert.equal(posts, 1) }
  }
})
test('actual SFC new receipt clears prior selected names and never displays mismatched result balances', async () => {
  const scenario = setup(), started = scenario.store.begin(scenario.task.id, permit)
  scenario.store.settle(scenario.task.id, 'succeeded', '', success(started))
  const api = { async get(path) { if (path.startsWith('/material-receipts/')) return { data: { ...detail(started), supplier_id: 112 } }; return { data: realReads(path) } } }
  const state = component(api, scenario)
  await state.refreshResult()
  assert.match(state.error.value, /身份不符/); assert.equal(state.detail.value, null); assert.equal(state.stock.value, null)
  assert.equal(state.store.get(state.task.id).result.movementId, null)
  state.material.value = { name: 'old material' }; state.supplier.value = { name: 'old supplier' }
  const next = state.store.create(receipt.receiptKind, 'new', '/m/jobs')
  state.route.params.jobId = next.id; state.route.params.step = 'form'; state.touch(); state.restore()
  assert.equal(state.material.value, null); assert.equal(state.supplier.value, null); assert.equal(state.form.materialId, '')
})
