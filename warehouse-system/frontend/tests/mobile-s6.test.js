import test from 'node:test'
import assert from 'node:assert/strict'
import { createTaskStore } from '../src/mobile/taskState.js'
import { locationFields, locationSnapshot, locationPayload, locationSuccess, locationConfirmation, readLocationSelection, readLocationResult, materialRows } from '../src/mobile/locationJobs.js'
import { completedTaskLocation, mobileParentLocation } from '../src/mobile/navigation.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'
const fields = { itemId: '3', batchId: '7', locationId: '8', targetLocationId: '9', unit: '桶', quantity: '1.000001', notes: '移库' }
function setup(kind = 'material-transfer', values = fields) {
  const data = new Map(), storage = { getItem: k => data.get(k), setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) }
  let seq = 0
  const options = { storage, instance: 's6', user: 1, newId: () => `id-${++seq}` }
  const store = createTaskStore(options), task = store.selectLocation(store.create(kind, 'new', '/m/inventory?page=6').id, values)
  return { store, task, options, data }
}
const permit = { ready: true, allowed: true, bookQuantity: '2.000001' }
test('S6 per-business drafts exclude book/balance/credentials; count zero request survives reload', () => {
  const { store, task, options, data } = setup('material-count', { ...fields, quantity: '0' })
  store.edit(task.id, { ...fields, quantity: '0', bookQuantity: '98765', password: 'secret', token: 'secret' })
  assert.ok(![...data.values()].join('').includes('secret')); assert.ok(![...data.values()].join('').includes('98765'))
  assert.ok(!Object.hasOwn(store.get(task.id).fields, 'targetLocationId'))
  const begun = store.begin(task.id, permit)
  assert.deepEqual(locationPayload(begun), { request_id: begun.requestId, batch_id: 7, book_quantity: '2.000001', actual_quantity: '0', reason: '移库' })
  assert.equal(createTaskStore(options).get(task.id).status, 'uncertain')
  assert.equal(createTaskStore(options).get(task.id).snapshot.bookQuantity, '2.000001')
})
test('S6 decimal bounds and original payload semantics reject same target and invalid actual', () => {
  for (const quantity of ['-1', '1e3', '1.0000001', '100000000000000']) for (const kind of ['material-count', 'material-transfer']) assert.throws(() => locationSnapshot(kind, { ...fields, quantity }, '2'))
  assert.throws(() => locationSnapshot('material-transfer', { ...fields, quantity: '0' }))
  assert.throws(() => locationSnapshot('material-transfer', { ...fields, targetLocationId: '8' }))
  assert.equal(locationSnapshot('material-count', { ...fields, quantity: '0.000001' }, '0').quantity, '0.000001')
  assert.equal(locationSnapshot('material-count', { ...fields, quantity: '99999999999999.999999' }, '0').quantity, '99999999999999.999999')
  const { store, task } = setup(); const p = locationPayload(store.begin(task.id, permit))
  assert.ok(!Object.hasOwn(p, 'unit')); assert.equal(p.target_location_id, 9); assert.equal(p.quantity, '1.000001')
})
test('S6 independent unknown contexts cannot bypass exact record by new job; success is tombstoned', () => {
  const { store, task, options } = setup(), begun = store.begin(task.id, permit)
  store.suspend()
  const newTask = store.create(task.kind, 'new', '/m/jobs')
  assert.equal(store.selectLocation(newTask.id, fields).id, task.id)
  assert.notEqual(store.create('material-count', task.record, '/m/jobs').id, task.id)
  assert.throws(() => store.begin(task.id, permit))
  assert.throws(() => locationSuccess(begun, { request_id: 'wrong', id: 40, document_no: 'MT-40' }))
  store.settle(task.id, 'succeeded', '', { request_id: begun.requestId, id: 40, document_no: 'MT-40', kind: 'transfer', source_batch_id: 7, target_batch_id: 10, quantity: '1.000001' })
  assert.equal(createTaskStore(options).get(task.id).status, 'succeeded'); assert.equal(store.get(task.id).fields.quantity, '')
  assert.throws(() => store.begin(task.id, permit))
  const fresh = store.selectLocation(newTask.id, fields), next = store.begin(fresh.id, permit)
  assert.notEqual(next.id, task.id); assert.notEqual(next.requestId, begun.requestId)
  assert.equal(completedTaskLocation({ params: { step: 'form' } }, store.get(task.id)).path, `/m/jobs/material-transfer/${task.id}/result`)
})
test('S6 count success binds book actual difference and match kind; zero has no phantom movement', () => {
  const { store, task, options } = setup('material-count', { ...fields, quantity: '0', notes: '' }), begun = store.begin(task.id, { ...permit, bookQuantity: '0' })
  const result = { request_id: begun.requestId, id: 45, document_no: 'MC-45', kind: 'count_match', batch_id: 7, book_quantity: '0', actual_quantity: '0', difference: '0' }
  assert.throws(() => locationSuccess(begun, { ...result, kind: 'count_loss' }))
  store.settle(task.id, 'succeeded', '', result)
  assert.equal(createTaskStore(options).healthy, true)
  assert.equal(createTaskStore(options).get(task.id).status, 'succeeded')
})
test('S6 source/target revalidation reads zero targets beyond100 and keeps date/unit attribution', async () => {
  let source = { id: 7, material_id: 3, supplier_id: 2, location_id: 8, unit: '桶', batch_no: 'B', production_date: '2026-01-01', expiry_date: '2026-02-01', remaining_quantity: '2' }
  const target = { ...source, id: 10, location_id: 9, remaining_quantity: '0', received_quantity: '1' }
  const calls = [], records = { '/materials/3': { id: 3, unit: '桶', is_active: true }, '/suppliers/2': { id: 2, is_active: true }, '/locations/8': { id: 8, is_active: true }, '/locations/9': { id: 9, is_active: true } }
  const api = { get: async (path, options) => { calls.push([path, options]); return { data: path === '/material-location-stock/7' ? source : path === '/material-batches' ? { total: 101, items: options.params.page === 1 ? Array.from({ length: 100 }, (_, i) => ({ ...target, id: i + 100, batch_no: 'other' })) : [target] } : records[path] } } }
  const selection = await readLocationSelection(api, 'material-transfer', fields)
  assert.equal(selection.targetBatch.id, 10); assert.ok(calls.some(([path, o]) => path === '/material-batches' && o.params.page === 2))
  source = { ...source, remaining_quantity: '1' }; await assert.rejects(readLocationSelection(api, 'material-transfer', fields), /旧数量/)
  source = { ...source, remaining_quantity: '2' }; target.expiry_date = '2026-03-01'; await assert.rejects(readLocationSelection(api, 'material-transfer', fields), /日期不一致/)
  records['/locations/9'].is_active = false; await assert.rejects(readLocationSelection(api, 'material-transfer', fields), /停用/)
})
test('S6 confirmation detects even still-sufficient balance and target changes without replacing actual', () => {
  const { task } = setup(), s = { row: { remaining_quantity: '5', batch_no: 'B', supplier_id: 2, production_date: '2026-01-01', expiry_date: '2026-02-01' }, targetBatch: null }
  const before = locationConfirmation(task, s)
  assert.notEqual(locationConfirmation(task, { ...s, row: { ...s.row, remaining_quantity: '4' } }), before)
  assert.notEqual(locationConfirmation(task, { ...s, targetBatch: { id: 10, received_quantity: '1', remaining_quantity: '0' } }), before)
  assert.equal(task.fields.quantity, '1.000001')
})
test('S6 explicit success stays completed after result query failure; no approximate adoption', async () => {
  const { store, task } = setup(), begun = store.begin(task.id, permit)
  store.settle(task.id, 'succeeded', '', { request_id: begun.requestId, id: 40, document_no: 'MT-40', kind: 'transfer', source_batch_id: 7, target_batch_id: 10, quantity: '1.000001' })
  await assert.rejects(readLocationResult({ get: async () => { throw new Error('offline') } }, store.get(task.id)), /offline/)
  assert.equal(store.get(task.id).status, 'succeeded')
  await assert.rejects(readLocationResult({ get: async () => ({ data: { id: 41, document_no: 'MT-40', kind: 'transfer', notes: '移库', transactions: [] } }) }, store.get(task.id)), /身份/)
})
test('S6 navigation preserves list origin and exact login callback', () => {
  const { task } = setup()
  assert.equal(mobileParentLocation({ meta: { mobileLocationTask: true }, params: { step: 'confirm' }, query: {} }, task), `/m/jobs/material-transfer/${task.id}/form`)
  for (const kind of ['material-transfer', 'material-count']) {
    assert.equal(mobileLoginTarget(`/m/jobs/${kind}?from=%2Fm%2Frecords`), `/m/jobs/${kind}?from=%2Fm%2Frecords`)
    assert.equal(mobileLoginTarget(`/m/jobs/${kind}/${task.id}/result`), `/m/jobs/${kind}/${task.id}/result`)
  }
  assert.ok(!Object.hasOwn(locationFields('material-count', { ...fields, bookQuantity: '2' }), 'bookQuantity'))
})
test('S6 exact count result search traverses more than100 rows; zero difference has no ledger', async () => {
  const { store, task } = setup('material-count', { ...fields, quantity: '0', notes: '' }), begun = store.begin(task.id, { ...permit, bookQuantity: '0' })
  store.settle(task.id, 'succeeded', '', { request_id: begun.requestId, id: 45, document_no: 'MC-45', kind: 'count_match', batch_id: 7, book_quantity: '0', actual_quantity: '0', difference: '0' })
  const calls = [], count = { id: 50, document_id: 45, batch_id: 7, unit: '桶', book_quantity: '0', actual_quantity: '0', difference: '0', reason: null }
  const records = { '/stock-documents/45': { id: 45, document_no: 'MC-45', kind: 'count_match', notes: null, transactions: [] }, '/material-location-stock/7': { id: 7, material_id: 3, location_id: 8, supplier_id: 2, unit: '桶' }, '/materials/3': { id: 3, unit: '桶' }, '/locations/8': { id: 8 }, '/suppliers/2': { id: 2 } }
  const api = { get: async (path, options) => { calls.push([path, options]); return { data: path === '/material-counts' ? { total: 101, items: options.params.page === 1 ? [{ ...count, document_id: 99 }] : [count] } : path === '/material-batches' ? { total: 1, items: [records['/material-location-stock/7']] } : records[path] } } }
  const data = await readLocationResult(api, store.get(task.id))
  assert.equal(data.count.id, 50); assert.equal(data.document.transactions.length, 0)
  assert.ok(calls.some(([path, options]) => path === '/material-counts' && options.params.page === 2))
  count.document_id = 99; await assert.rejects(readLocationResult(api, store.get(task.id)), /未读取/)
})
test('S6 transfer result ties exact source and target IDs to immutable conserving ledger', async () => {
  const { store, task } = setup(), begun = store.begin(task.id, permit)
  store.settle(task.id, 'succeeded', '', { request_id: begun.requestId, id: 40, document_no: 'MT-40', kind: 'transfer', source_batch_id: 7, target_batch_id: 10, quantity: '1.000001' })
  const source = { id: 7, material_id: 3, location_id: 8, supplier_id: 2, unit: '桶', batch_no: 'B', production_date: '2026-01-01', expiry_date: '2026-02-01' }
  const target = { ...source, id: 10, location_id: 9 }
  const document = { id: 40, document_no: 'MT-40', kind: 'transfer', notes: '移库', transactions: [{ material_batch_id: 7, unit: '桶', before_quantity: '2', change_quantity: '-1.000001', after_quantity: '0.999999' }, { material_batch_id: 10, unit: '桶', before_quantity: '0', change_quantity: '1.000001', after_quantity: '1.000001' }] }
  const records = { '/stock-documents/40': document, '/material-location-stock/7': source, '/material-location-stock/10': target, '/materials/3': { id: 3, unit: '桶' }, '/locations/8': { id: 8 }, '/locations/9': { id: 9 }, '/suppliers/2': { id: 2 } }
  const api = { get: async path => ({ data: path === '/material-batches' ? { total: 2, items: [source, target] } : records[path] }) }
  assert.equal((await readLocationResult(api, store.get(task.id))).target.id, 10)
  target.location_id = 8; await assert.rejects(readLocationResult(api, store.get(task.id)), /归属/)
  target.location_id = 9; document.transactions[1].change_quantity = '2'; await assert.rejects(readLocationResult(api, store.get(task.id)), /守恒/)
})
