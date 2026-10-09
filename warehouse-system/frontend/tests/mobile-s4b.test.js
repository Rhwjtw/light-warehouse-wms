import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { createTaskStore, lockedTask, taskLabels } from '../src/mobile/taskState.js'
import * as putaway from '../src/mobile/putaway.js'
import { quantityUnits } from '../src/quantity.js'
import { mobileParentLocation, completedTaskLocation, returnToParent } from '../src/mobile/navigation.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'
import { readGate, positiveId } from '../src/mobile/readQueries.js'

const row = { id: 501, receipt_movement_id: 901, batch_id: 701, unit: '桶', pending_quantity: '12.300001', status: 'partial', material_id: 107, supplier_id: 111 }
const fields = { receiptId: '501', movementId: '901', sourceBatchId: '701', locationId: '31', unit: '桶', quantity: '2.300001', notes: '备注' }
const permit = { ready: true, allowed: true }
function setup() {
  const values = new Map(), storage = { getItem: k => values.get(k), setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) }
  let seq = 0
  const config = { storage, instance: 'isolated', user: 9, newId: () => `request-${++seq}` }, store = createTaskStore(config)
  const task = store.selectPutaway(store.create(putaway.putawayKind, 'new', '/m/pending?q=原料&page=6').id, row)
  store.edit(task.id, fields)
  return { store, task, config, values }
}
const success = task => ({ id: 601, document_no: 'MP-601', request_id: task.requestId, kind: 'putaway', source_batch_id: 701, target_batch_id: 801, quantity: task.snapshot.quantity, token: 'discard', remaining_quantity: 'discard' })
test('putaway allowlist, immutable request result identity, decimal strings and strict receipt pair', () => {
  const { store, task, config, values } = setup()
  store.edit(task.id, { ...fields, notes: '备'.repeat(2000), password: 'secret', token: 'secret', pending_quantity: '88888' })
  assert.ok(![...values.values()].join('').includes('secret')); assert.ok(![...values.values()].join('').includes('88888'))
  assert.equal(createTaskStore(config).get(task.id).fields.notes.length, 2000)
  for (const q of ['0', '-1', '1e2', '0.0000001', '100000000000000']) assert.throws(() => putaway.putawaySnapshot({ ...fields, quantity: q }))
  assert.equal(putaway.putawaySnapshot({ ...fields, quantity: '12.300000' }).quantity, '12.3')
  const started = store.begin(task.id, permit)
  assert.deepEqual(Object.keys(putaway.putawayPayload(started)).sort(), ['request_id','receipt_movement_id','location_id','quantity','notes'].sort())
  assert.throws(() => putaway.verifyPutawayReceipt(fields, { ...row, receipt_movement_id: 902 }))
  assert.throws(() => putaway.verifyPutawayReceipt(fields, { ...row, pending_quantity: '0' }, true))
  assert.throws(() => store.settle(task.id, 'succeeded', '', { ...success(started), source_batch_id: 702 }))
  store.settle(task.id, 'succeeded', '', success(started))
  assert.deepEqual(createTaskStore(config).get(task.id).result, { id: 601, documentNo: 'MP-601', targetBatchId: 801 })
  assert.throws(() => store.begin(task.id, permit)); assert.equal(store.get(task.id).fields.quantity, '')
})
test('same record unknown cannot be bypassed by blank task; changed receipt clears location quantity and key', () => {
  const { store, task, config } = setup(), first = store.begin(task.id, permit)
  store.suspend()
  const restored = createTaskStore(config), blank = restored.create(putaway.putawayKind, 'new', '/m/jobs')
  assert.equal(restored.selectPutaway(blank.id, row).id, task.id)
  assert.equal(restored.get(task.id).requestId, first.requestId)
  assert.throws(() => restored.begin(task.id, permit))
  const other = restored.selectPutaway(blank.id, { ...row, id: 502, receipt_movement_id: 902, batch_id: 702 })
  assert.equal(other.fields.quantity, ''); assert.equal(other.fields.locationId, '')
  restored.edit(other.id, { ...other.fields, quantity: '1', locationId: '32' })
  const changed = restored.selectPutaway(other.id, { ...row, id: 503, receipt_movement_id: 903, batch_id: 703 })
  assert.equal(changed.fields.quantity, ''); assert.equal(changed.fields.locationId, ''); assert.equal(changed.requestId, null)
})
test('continuation creates fresh request; rejection retry preserves key; navigation closes stale successful form', () => {
  const { store, task } = setup(), first = store.begin(task.id, permit)
  store.settle(task.id, 'rejected'); assert.equal(store.begin(task.id, permit).requestId, first.requestId)
  store.settle(task.id, 'succeeded', '', success(first))
  const next = store.selectPutaway(store.create(putaway.putawayKind, `new:${task.id}`, task.origin).id, row)
  assert.notEqual(next.id, task.id); assert.equal(next.fields.quantity, ''); assert.equal(next.fields.locationId, '')
  store.edit(next.id, { ...next.fields, quantity: '1', locationId: '32' })
  assert.notEqual(store.begin(next.id, permit).requestId, first.requestId)
  assert.deepEqual(completedTaskLocation({ params: { step: 'form' } }, store.get(task.id)), { path: putaway.putawayTaskPath(task, 'result'), replace: true })
  assert.equal(mobileParentLocation({ meta: { mobilePutawayTask: true }, params: { step: 'confirm' }, query: {} }, next), putaway.putawayTaskPath(next))
  assert.equal(mobileLoginTarget(putaway.putawayTaskPath(task, 'result')), putaway.putawayTaskPath(task, 'result'))
})
function component(api, state = setup()) {
  const { store, task } = state
  const route = { params: { jobId: task.id, step: 'confirm' }, path: putaway.putawayTaskPath(task, 'confirm'), fullPath: putaway.putawayTaskPath(task, 'confirm'), query: {} }
  const calls = [], router = { replace: async x => { calls.push(x); route.params.step = 'result' }, push: async x => calls.push(x), options: { history: { state: {} } } }, auth = { user: { id: 9, role: 'admin' } }
  const code = parse(readFileSync(new URL('../src/mobile/MobilePutaway.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const deps = { ...putaway, computed, reactive, ref, nextTick: async () => {}, onMounted() {}, onBeforeUnmount() {}, watch() {}, useRoute: () => route, useRouter: () => router, api, auth, canOperate: () => true, mobileRevision: ref(0), mobileStore: () => store, touchMobile() {}, lockedTask, taskLabels, readGate, positiveId, quantityUnits, returnToParent }
  deps.touchMobile = () => deps.mobileRevision.value++
  return { ...new Function(...Object.keys(deps), code + '\nloadFields(); return {submit,confirm,refreshResult,form,error,result,resume,restore,start,check}')(...Object.values(deps)), store, task, route, calls, touch: deps.touchMobile }
}
function reads(path, pending = '12.300001') {
  if (path === '/auth/me') return { id: 9, role: 'admin' }
  if (path === '/material-receipts/501') return { ...row, pending_quantity: pending }
  if (path === '/materials/107') return { id: 107, unit: '桶', is_active: true }
  if (path === '/suppliers/111') return { id: 111, is_active: true }
  if (path === '/locations/31') return { id: 31, is_active: true }
  throw new Error('GET result unavailable')
}
test('actual SFC serializes async double clicks, keeps success on failed result GET and never resubmits', async () => {
  let release, posts = 0
  const api = { async get(path) { return { data: reads(path) } }, async post(_path,payload) { posts++; await new Promise(r => release = r); return { data: success({ requestId: payload.request_id, snapshot: fields }) } } }
  const state = component(api), pending = state.submit(); await state.submit(); await new Promise(r => setTimeout(r,0))
  assert.equal(posts,1); release(); await pending
  assert.equal(state.store.get(state.task.id).status,'succeeded'); assert.match(state.error.value,/已成功.*待刷新/); assert.equal(state.result.value,null)
  await state.submit(); assert.equal(posts,1)
})
test('actual SFC rereads stale remaining and inactive location before POST; same continuation cannot consume zero', async () => {
  for (const mode of ['stale','inactive']) {
    let posts=0
    const state=component({ async get(path) { return { data: mode === 'inactive' && path === '/locations/31' ? { id:31,is_active:false } : reads(path, mode === 'stale' ? '1' : '12.300001') } }, async post() { posts++ } })
    await state.submit(); assert.equal(posts,0); assert.equal(state.store.get(state.task.id).status,'draft')
    assert.match(state.error.value,mode === 'stale' ? /超过最新待上架量/ : /库位已停用/)
  }
  const state=setup(), started=state.store.begin(state.task.id,permit);state.store.settle(state.task.id,'succeeded','',success(started))
  const view=component({ async get(path){return {data:reads(path,'0')}} },state)
  await view.start(true);assert.equal(view.calls.length,0);assert.match(view.error.value,/已全部上架/)
})
test('actual SFC response loss, auth, conflict or server failure stays unknown; resume does only GET', async () => {
  for (const status of [undefined,401,409,503,403,422]) {
    let posts=0
    const state=component({async get(path){return {data:reads(path)}},async post(){posts++;throw Object.assign(new Error('lost/rejected'),{status})}})
    await state.submit(); assert.equal(state.store.get(state.task.id).status,[403,422].includes(status)?'rejected':'uncertain')
    state.resume();await new Promise(r=>setTimeout(r,0));assert.equal(posts,1)
    if (![403,422].includes(status)){await state.submit();assert.equal(posts,1)}
  }
})
test('result verifies exact document and transfer allocation, pages all relevant location stocks beyond 100', async () => {
  const {store,task}=setup(),started=store.begin(task.id,permit);store.settle(task.id,'succeeded','',success(started))
  const responses={ '/material-receipts/501':{...row,allocations:[{document_id:601,quantity:'2.300001'}]}, '/stock-documents/601':{id:601,kind:'putaway',document_no:'MP-601',transactions:[{material_batch_id:701,change_quantity:'-2.300001'},{material_batch_id:801,change_quantity:'2.300001'}]}, '/material-location-stock/801':{id:801,location_id:31,material_id:107,unit:'桶'} }
  const pages=[];const api={async get(path,opts){if(path==='/material-location-stock'){pages.push(opts.params.page);return {data:{total:101,items:[{id:opts.params.page}]}}}return {data:responses[path]}}}
  assert.equal((await putaway.readPutawayResult(api,store.get(task.id))).stocks.length,2);assert.deepEqual(pages,[1,2])
  responses['/material-receipts/501'].allocations[0].document_id=602
  await assert.rejects(()=>putaway.readPutawayResult(api,store.get(task.id)),/身份不符/)
})
