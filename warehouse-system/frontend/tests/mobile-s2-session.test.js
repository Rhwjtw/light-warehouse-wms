import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive, ref, watch } from 'vue'
import { createTaskStore } from '../src/mobile/taskState.js'

function session(storage = {}) {
  let sequence = 0
  storage.getItem ||= key => storage[key] || null
  storage.setItem ||= (key, value) => { storage[key] = value }
  storage.removeItem ||= key => { delete storage[key] }
  const listeners = [], auth = reactive({ user: null })
  const window = { sessionStorage: storage, location: { origin: 'https://s2.test' }, addEventListener: (name, fn, options) => listeners.push({ name, fn, capture: options?.capture }) }
  // 模拟既有auth在先注册的普通监听；DOM事件在目标先执行capture再执行普通监听。
  window.addEventListener('wms-auth-expired', () => { auth.user = null })
  let source = readFileSync(new URL('../src/mobile/session.js', import.meta.url), 'utf8')
  source = source.replace(/^import .*$/gm, '').replace(/^export /gm, '').replaceAll('import.meta.env.VITE_API_BASE_URL', "'/api'")
  const state = new Function('ref', 'watch', 'auth', 'createTaskStore', 'window', 'crypto', source + '\nreturn {mobileStore,clearMobileSession,pendingMobileTasks}')(
    ref, watch, auth, createTaskStore, window, { randomUUID: () => `job-${++sequence}` },
  )
  return { ...state, auth, storage, expire() { for (const capture of [true, false]) for (const listener of listeners) if (listener.name === 'wms-auth-expired' && Boolean(listener.capture) === capture) listener.fn(new Event('wms-auth-expired')) } }
}
function draft(state) {
  state.auth.user = { id: 1, role: 'admin' }
  const store = state.mobileStore(), task = store.create('s2-prototype', 'S2-1', '/m/state-lab')
  store.edit(task.id, { quantity: '1.000001', location: 'A' })
  return { store, task }
}

test('actual session observer hides expired user, preserves original request and restores only same authenticated user', () => {
  const state = session(), { store, task } = draft(state)
  const started = store.begin(task.id, { ready: true, allowed: true })
  state.expire(); assert.equal(state.mobileStore(), null); assert.equal(state.pendingMobileTasks().length, 0)
  state.auth.user = { id: 1, role: 'admin' }
  assert.equal(state.mobileStore().get(task.id).status, 'uncertain')
  assert.equal(state.mobileStore().get(task.id).requestId, started.requestId)
  state.auth.user = { id: 2, role: 'warehouse' }
  assert.equal(state.mobileStore().tasks().length, 0)
  assert.equal(Object.keys(state.storage).filter(key => key.startsWith('wms.mobile.v1:') && key.includes('"1"')).length, 0)
})

test('explicit logout clears storage despite late expiry notification and cannot restore old task', () => {
  const state = session(), { task } = draft(state)
  state.expire(); state.auth.user = { id: 1 }
  state.clearMobileSession(state.auth.user.id); state.expire()
  state.auth.user = { id: 1 }
  assert.equal(state.mobileStore().get(task.id), null)
})

test('after reload, PC logout removes saved mobile namespace and different login cleans prior owner', () => {
  const old = session(); draft(old)
  const pc = session(old.storage); pc.auth.user = { id: 1 }
  pc.clearMobileSession(pc.auth.user.id); pc.auth.user = null
  assert.equal(Object.keys(old.storage).filter(key => key.startsWith('wms.mobile.v1:')).length, 0)
  const restored = session(old.storage); draft(restored)
  const other = session(old.storage); other.auth.user = { id: 2 }
  assert.equal(Object.keys(old.storage).filter(key => key.startsWith('wms.mobile.v1:') && key.includes('"1"')).length, 0)
})
