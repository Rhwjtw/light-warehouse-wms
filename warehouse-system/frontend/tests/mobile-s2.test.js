import test from 'node:test'
import assert from 'node:assert/strict'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTaskStore } from '../src/mobile/taskState.js'
import { completedTaskLocation, listQuery, mobileParentLocation, prototypeListLocation, returnToParent } from '../src/mobile/navigation.js'
import { attachMobileLifecycle, viewportState } from '../src/mobile/lifecycle.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'

function memory() { const rows = new Map(); return { getItem: key => rows.get(key) || null, setItem: (key, value) => rows.set(key, value), removeItem: key => rows.delete(key), rows } }
function setup(storage = memory(), options = {}) {
  let seq = 0
  const config = { storage, instance: 'https://isolated.test|/api', user: 11, newId: () => `id-${++seq}`, ...options }
  const store = createTaskStore(config), task = store.create('s2-prototype', 'S2-1', '/m/state-lab?group=A&page=2')
  const fields = { quantity: '1.000001', location: '演练库位A', notes: '备注', password: 'must-not-save', token: 'must-not-save' }
  store.edit(task.id, fields)
  return { store, task, fields, storage, config }
}
const permit = { ready: true, allowed: true }

test('draft persists each edit across reload, allowlists fields, isolates instance/user/kind/record', () => {
  const { store, task, fields, storage, config } = setup()
  assert.equal(createTaskStore(config).get(task.id).fields.quantity, fields.quantity)
  assert.ok(![...storage.rows.values()].join('').includes('must-not-save'))
  assert.equal(createTaskStore({ ...config, user: 12 }).get(task.id), null)
  assert.equal(createTaskStore({ ...config, instance: 'https://other.test' }).get(task.id), null)
  assert.notEqual(store.create('different', 'S2-1', '/m/state-lab').id, task.id)
  assert.notEqual(store.create('s2-prototype', 'S2-2', '/m/state-lab').id, task.id)
  store.clear(); assert.equal(createTaskStore(config).get(task.id), null)
})

test('double begin and edits cannot replace submitting request or normalized snapshot', () => {
  const { store, task } = setup()
  const started = store.begin(task.id, permit)
  assert.throws(() => store.begin(task.id, permit), /不可/)
  assert.throws(() => store.edit(task.id, { quantity: '2' }), /不可/)
  assert.equal(store.get(task.id).requestId, started.requestId)
  assert.equal(store.get(task.id).snapshot.quantity, '1.000001')
})

test('success invalidates fields permanently across refresh/history; new task uses new key', () => {
  const { store, task, config } = setup()
  const before = store.begin(task.id, permit)
  assert.equal(store.settle(task.id, 'succeeded'), true)
  assert.equal(store.settle(task.id, 'uncertain'), false)
  const restored = createTaskStore(config)
  assert.equal(restored.get(task.id).fields.quantity, '')
  assert.throws(() => restored.edit(task.id, { quantity: '3' }), /不可/)
  assert.throws(() => restored.begin(task.id, permit), /不可/)
  const next = store.create('s2-prototype', 'S2-1', '/m/state-lab')
  store.edit(next.id, { quantity: '2', location: 'A' })
  assert.notEqual(store.begin(next.id, permit).requestId, before.requestId)
})

test('response loss, background and reload preserve uncertain context and deny retry/new task', () => {
  for (const mode of ['loss', 'background', 'reload']) {
    const { store, task, config } = setup()
    const request = store.begin(task.id, permit)
    if (mode === 'loss') store.settle(task.id, 'uncertain', 'timeout')
    if (mode === 'background') store.suspend()
    const restored = createTaskStore(config)
    assert.equal(restored.get(task.id).status, 'uncertain')
    assert.equal(restored.get(task.id).requestId, request.requestId)
    assert.deepEqual(restored.get(task.id).snapshot, request.snapshot)
    assert.equal(restored.create('s2-prototype', 'S2-1', '/m/state-lab').id, task.id)
    assert.throws(() => restored.begin(task.id, permit), /不可/)
    assert.equal(restored.settle(task.id, 'rejected'), false)
  }
})

test('late explicit success closes uncertain context but arbitrary new failure cannot unlock it', () => {
  const { store, task } = setup()
  store.begin(task.id, permit); store.suspend()
  assert.equal(store.settle(task.id, 'rejected'), false)
  assert.equal(store.settle(task.id, 'succeeded'), true)
  assert.equal(store.get(task.id).status, 'succeeded')
})

test('explicit rejection is editable; unchanged retry keeps key; changed content uses new key', () => {
  const { store, task, fields } = setup()
  const first = store.begin(task.id, permit)
  store.settle(task.id, 'rejected', '明确拒绝')
  store.edit(task.id, fields)
  assert.equal(store.begin(task.id, permit).requestId, first.requestId)
  store.settle(task.id, 'rejected')
  store.edit(task.id, { ...fields, quantity: '2.000001' })
  assert.notEqual(store.begin(task.id, permit).requestId, first.requestId)
})

test('permissions/revalidation, precision and size reject before request starts without rounding', () => {
  const { store, task, fields } = setup()
  for (const flags of [{ ready: false, allowed: true }, { ready: true, allowed: false }]) assert.throws(() => store.begin(task.id, flags), /核实/)
  for (const quantity of ['0', '-1', '1.0000001', '1e3', '100000000000000', '1,000', '']) {
    store.edit(task.id, { ...fields, quantity }); assert.throws(() => store.begin(task.id, permit))
    assert.equal(store.get(task.id).status, 'draft')
  }
  store.edit(task.id, { ...fields, quantity: '99999999999999.000001' })
  assert.equal(store.begin(task.id, permit).snapshot.quantity, '99999999999999.000001')
})

test('denied/quota/corrupt storage warns, keeps current edits and cannot submit', () => {
  for (const storage of [{ getItem() { throw Error('denied') } }, { getItem: () => '{invalid' }, { getItem: () => '{"tasks":[],"lists":{}}' }, { getItem: () => null, setItem() { throw Error('quota') } }]) {
    const { store, task } = setup(storage)
    assert.equal(store.healthy, false); assert.match(store.warning, /存储/)
    assert.equal(store.get(task.id).fields.quantity, '1.000001')
    assert.throws(() => store.begin(task.id, permit), /存储/)
  }
})

test('invalid deep-link names never expose object prototypes or rebuild a missing task', () => {
  const { store } = setup()
  for (const id of ['constructor', '__proto__', 'missing']) assert.equal(store.get(id), null)
})

test('quota during begin never reports submitting or success', () => {
  const { store, task, storage } = setup()
  storage.setItem = () => { throw Error('quota') }
  assert.throws(() => store.begin(task.id, permit), /存储/)
  assert.equal(store.get(task.id).status, 'draft')
})

test('list position and query are separate from drafts; safe parent rejects external URLs', () => {
  const { store, task } = setup()
  store.saveList(task.origin, { top: 320, anchor: 'S2-9' })
  assert.deepEqual(store.list(task.origin), { top: 320, anchor: 'S2-9' })
  assert.deepEqual(store.list('/m/state-lab'), { top: 0, anchor: '' })
  assert.deepEqual(listQuery({ q: '08', group: 'A', page: '2' }), { q: '08', group: 'A', page: 2 })
  assert.deepEqual(listQuery({ q: ['x'], group: 'bad', page: '-1' }), { q: '', group: 'all', page: 1 })
  for (const value of ['https://evil.test', '//evil.test', '/m/account', '/m/\\evil']) assert.equal(prototypeListLocation(value), '/m/state-lab')
  const route = { meta: { mobileLabTask: true }, params: { step: 'confirm' } }
  assert.equal(mobileParentLocation(route, task), `/m/state-lab/${task.id}/form`)
  assert.equal(mobileParentLocation(route, { ...task, status: 'succeeded' }), task.origin)
  assert.equal(mobileParentLocation({ meta: { mobileParent: '/m/jobs' } }), '/m/jobs')
})

test('normal return consumes parent history; completed redirects replace stale entries so repeated back can leave task', async () => {
  const calls = [], router = { options: { history: { state: { back: '/m/state-lab?page=2' } } }, back: () => calls.push('back'), replace: target => calls.push(target) }
  returnToParent(router, '/m/state-lab?page=2'); returnToParent(router, '/m/jobs')
  assert.deepEqual(calls, ['back', '/m/jobs'])
  assert.equal(mobileLoginTarget('/m/state-lab/id-1/confirm'), '/m/state-lab/id-1/confirm')
  assert.equal(mobileLoginTarget('/m/state-lab/id-1/unknown'), '/m')
  const history = createMemoryHistory(), flow = createRouter({ history, routes: [{ path: '/m/state-lab', component: {} }, { path: '/m/state-lab/:jobId/:step', component: {} }] })
  let succeeded = false
  flow.beforeEach(to => to.params.jobId ? completedTaskLocation(to, { id: 'id-1', status: succeeded ? 'succeeded' : 'draft' }) || true : true)
  await flow.push('/m/state-lab'); await flow.push('/m/state-lab/id-1/form'); await flow.push('/m/state-lab/id-1/confirm')
  succeeded = true; await flow.replace('/m/state-lab/id-1/result')
  function back() { return new Promise((resolve, reject) => { const remove = flow.afterEach((to, from, failure) => { if (failure) return; remove(); resolve(to) }); flow.back(); setTimeout(() => { remove(); reject(new Error('history did not progress')) }, 1000).unref() }) }
  await back(); assert.equal(flow.currentRoute.value.params.step, 'result')
  await back(); assert.equal(flow.currentRoute.value.path, '/m/state-lab')
})

test('background stops resources/suspends; foreground/online/BFCache only invoke read recovery; cleanup removes hooks', () => {
  const win = new EventTarget(), doc = new EventTarget(), view = new EventTarget(), calls = []
  Object.assign(win, { visualViewport: view, innerHeight: 844 }); Object.assign(view, { height: 844, offsetTop: 0 })
  doc.visibilityState = 'visible'
  const cleanup = attachMobileLifecycle({ win, doc, suspend: () => calls.push('suspend'), resume: () => calls.push('read'), stopResources: () => calls.push('stop') })
  doc.visibilityState = 'hidden'; doc.dispatchEvent(new Event('visibilitychange'))
  doc.visibilityState = 'visible'; doc.dispatchEvent(new Event('visibilitychange')); win.dispatchEvent(new Event('online'))
  const event = new Event('pageshow'); event.persisted = true; win.dispatchEvent(event)
  assert.deepEqual(calls, ['stop', 'suspend', 'read', 'read', 'read'])
  cleanup(); const count = calls.length; win.dispatchEvent(new Event('online')); doc.dispatchEvent(new Event('visibilitychange')); assert.equal(calls.length, count)
})

test('keyboard viewport hides fixed navigation only for focused input; no viewport fallback stays scrollable', () => {
  assert.deepEqual(viewportState({ height: 420, offsetTop: 0 }, 844, true), { inset: 424, keyboard: true })
  assert.equal(viewportState({ height: 420, offsetTop: 0 }, 844, false).keyboard, false)
  assert.deepEqual(viewportState(null, 844, true), { inset: 0, keyboard: false })
})
