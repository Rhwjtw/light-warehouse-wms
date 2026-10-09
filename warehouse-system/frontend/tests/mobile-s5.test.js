import test from 'node:test'
import assert from 'node:assert/strict'
import { createTaskStore } from '../src/mobile/taskState.js'
import { stockFields, stockSnapshot, stockPayload, stockSuccess, readStockSelection, readStockResult } from '../src/mobile/stockJobs.js'
import { completedTaskLocation, mobileParentLocation } from '../src/mobile/navigation.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'

const fields = { itemId: '3', batchId: '7', locationId: '8', unit: '桶', quantity: '1.000001', notes: '出库' }
function setup(kind = 'material-outbound', f = fields) {
  const values = new Map(), storage = { getItem: k => values.get(k), setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) }
  let seq = 0
  const options = { storage, instance: 's5', user: 1, newId: () => `id-${++seq}` }
  const store = createTaskStore(options), task = store.selectStock(store.create(kind, 'new', '/m/inventory?page=6').id, f)
  return { store, task, options, values }
}
test('S5 business allowlists survive reload; arbitrary balances and credentials never persist', () => {
  const { store, task, options, values } = setup()
  store.edit(task.id, { ...fields, token: 'secret', remaining_quantity: '98765' })
  assert.equal(createTaskStore(options).healthy, true)
  assert.equal(createTaskStore(options).get(task.id).fields.unit, '桶')
  assert.ok(![...values.values()].join('').includes('secret'))
  assert.ok(![...values.values()].join('').includes('98765'))
  const a = stockFields({ allocations: [{ batchId: '3', scope: 'aggregate', quantity: '1', token: 'secret', balance: '999' }] })
  assert.deepEqual(a.allocations, [{ batchId: '3', scope: 'aggregate', quantity: '1' }])
})
test('S5 exact decimals, explicit allocation total and historical scope validation', () => {
  for (const quantity of ['0', '-1', '1e3', '1.0000001', '100000000000000']) assert.throws(() => stockSnapshot('material-outbound', { ...fields, quantity }))
  const fg = { itemId: '3', unit: '箱', quantity: '2.000001', businessDate: '2026-10-09', allocations: [{ batchId: '9', scope: 'batch', quantity: '1.000001' }, { batchId: '10', scope: 'aggregate', quantity: '1' }] }
  assert.equal(stockSnapshot('finished-outbound', fg).quantity, '2.000001')
  assert.throws(() => stockSnapshot('finished-outbound', { ...fg, quantity: '2' }))
  assert.throws(() => stockSnapshot('finished-outbound', { ...fg, allocations: [{ batchId: '9', scope: 'unknown', quantity: '2.000001' }] }))
  assert.throws(() => stockSnapshot('finished-receipt', fg))
  assert.throws(() => stockSnapshot('finished-receipt', { ...fg, allocations: [], businessDate: '2026-02-30' }))
})
test('S5 success tombstone binds exact request batch; continuation uses new job and key', () => {
  const { store, task, options } = setup()
  const begun = store.begin(task.id, { ready: true, allowed: true })
  const response = { request_id: begun.requestId, id: 44, document_no: 'RM-44', kind: 'material_outbound', batch_id: 7, purpose: 'normal', unit: '桶', quantity: '1.000001' }
  assert.throws(() => stockSuccess(begun, { ...response, batch_id: 6 }))
  store.settle(task.id, 'succeeded', '', response)
  assert.throws(() => store.begin(task.id, { ready: true, allowed: true }))
  assert.equal(createTaskStore(options).get(task.id).status, 'succeeded')
  assert.equal(store.get(task.id).fields.quantity, '')
  const next = store.create('material-outbound', 'new', '/m/jobs')
  assert.notEqual(next.id, task.id); assert.equal(next.requestId, null); assert.equal(next.fields.quantity, '')
  assert.deepEqual(completedTaskLocation({ params: { step: 'form' } }, store.get(task.id)), { path: `/m/jobs/material-outbound/${task.id}/result`, replace: true })
})
test('S5 unknown cannot be bypassed by selecting same stock; kinds remain isolated', () => {
  const { store, task, options } = setup()
  store.begin(task.id, { ready: true, allowed: true }); store.suspend()
  const fresh = store.create('material-outbound', 'new', '/m/jobs')
  assert.equal(store.selectStock(fresh.id, fields).id, task.id)
  assert.equal(createTaskStore(options).get(task.id).status, 'uncertain')
  assert.throws(() => store.edit(task.id, fields))
  assert.notEqual(store.create('finished-outbound', 'new', '/m/jobs').id, task.id)
  assert.equal(mobileLoginTarget(`/m/jobs/material-outbound/${task.id}/result`), `/m/jobs/material-outbound/${task.id}/result`)
  for (const kind of ['material-outbound', 'finished-outbound', 'finished-receipt']) assert.equal(mobileLoginTarget(`/m/jobs/${kind}?from=%2Fm%2Frecords`), `/m/jobs/${kind}?from=%2Fm%2Frecords`)
  assert.equal(mobileParentLocation({ meta: { mobileStockTask: true }, params: { step: 'confirm' }, query: {} }, { ...task, status: 'draft' }), `/m/jobs/material-outbound/${task.id}/form`)
})
test('S5 rereads exact material catalogs and refuses stale quantities, changed unit or expired stock', async () => {
  let row = { id: 7, material_id: 3, supplier_id: 2, location_id: 8, unit: '桶', remaining_quantity: '1', expired: false }
  const records = { '/materials/3': { id: 3, unit: '桶', is_active: true }, '/suppliers/2': { id: 2, is_active: true }, '/locations/8': { id: 8, is_active: true } }
  const api = { get: async path => ({ data: path.includes('material-location') ? row : records[path] }) }
  await assert.rejects(readStockSelection(api, 'material-outbound', fields), /旧数量/)
  row = { ...row, remaining_quantity: '2', expired: true }
  await assert.rejects(readStockSelection(api, 'material-outbound', fields), /过期/)
  row = { ...row, expired: false }; records['/materials/3'].unit = 'kg'
  await assert.rejects(readStockSelection(api, 'material-outbound', fields), /单位/)
})
test('S5 finished ordinary receipt sends no production or batch allocation and binds aggregate identity', () => {
  const { store, task } = setup('finished-receipt', { itemId: '3', unit: '箱', quantity: '12.300001', businessDate: '2026-10-09' })
  const begun = store.begin(task.id, { ready: true, allowed: true }), payload = stockPayload(begun)
  assert.equal(payload.business_type, 'normal'); assert.ok(!Object.hasOwn(payload, 'production_order_id')); assert.ok(!Object.hasOwn(payload, 'allocations'))
  const response = { id: 44, document_no: 'FG-44', request_id: begun.requestId, kind: 'finished_receipt', product_id: 3, quantity: '12.300001', unit: '箱', business_type: 'normal', business_date: '2026-10-09', allocations: [{ batch_id: 9, stock_scope: 'aggregate', quantity: '12.300001' }] }
  assert.throws(() => stockSuccess(begun, { ...response, allocations: [{ batch_id: 9, stock_scope: 'batch', quantity: '12.300001' }] }))
  store.settle(task.id, 'succeeded', '', response)
  assert.deepEqual(store.get(task.id).result.batchIds, [9])
})
test('S5 failed result GET cannot roll back explicit success; wrong document cannot be adopted', async () => {
  const { store, task } = setup()
  const begun = store.begin(task.id, { ready: true, allowed: true })
  store.settle(task.id, 'succeeded', '', { request_id: begun.requestId, id: 44, document_no: 'RM-44', kind: 'material_outbound', batch_id: 7, purpose: 'normal', unit: '桶', quantity: '1.000001' })
  await assert.rejects(readStockResult({ get: async () => { throw new Error('offline') } }, store.get(task.id)), /offline/)
  assert.equal(store.get(task.id).status, 'succeeded')
  await assert.rejects(readStockResult({ get: async () => ({ data: { id: 45, document_no: 'RM-44', kind: 'material_outbound', transactions: [] } }) }, store.get(task.id)), /身份/)
})
