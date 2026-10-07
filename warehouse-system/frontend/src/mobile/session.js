import { ref, watch } from 'vue'
import { auth } from '../auth'
import { createTaskStore } from './taskState.js'

export const mobileRevision = ref(0)
let store, currentUser, expired = false
const instance = () => `${window.location.origin}|${import.meta.env.VITE_API_BASE_URL || '/api'}`
function clearOtherUsers(user) {
  try {
    for (const key of Object.keys(window.sessionStorage)) {
      if (!key.startsWith('wms.mobile.v1:')) continue
      const [site, owner] = JSON.parse(key.slice('wms.mobile.v1:'.length))
      if (site === instance() && owner !== String(user)) window.sessionStorage.removeItem(key)
    }
  } catch { /* 存储不可读时由作业存储显示警告并阻止提交 */ }
}
// 本阶段以同源入口/API基址作为站点边界；原型业务另以 s2-prototype 隔离。
export function mobileStore() {
  const user = auth.user?.id
  if (user == null) return null
  if (currentUser !== user || !store) {
    store?.clear()
    let storage
    try { storage = window.sessionStorage } catch { storage = { getItem() { throw new Error('blocked') } } }
    store = createTaskStore({ storage, instance: instance(), user, newId: () => crypto.randomUUID() })
    currentUser = user
  }
  return store
}
export function touchMobile() { mobileRevision.value++ }
export function clearMobileSession(previousUser = currentUser) {
  store?.clear()
  // 刷新后从电脑版退出也清理已保存的手机草稿，不要求本轮先挂载手机Layout。
  if (previousUser != null && typeof previousUser !== 'object') {
    try { window.sessionStorage.removeItem(`wms.mobile.v1:${JSON.stringify([instance(), String(previousUser)])}`) } catch { /* 下次恢复会显示存储不可用 */ }
  }
  store = null; currentUser = undefined; expired = false; touchMobile()
}
watch(() => auth.user?.id, (next, previous) => {
  if (next != null) clearOtherUsers(next)
  // 401仅隐藏本账号上下文，重新认证同一账号后可恢复；主动退出/换账号仍清理。
  if (next == null && expired) { touchMobile(); return }
  if (next != null && next === currentUser) { expired = false; touchMobile(); return }
  if (next !== previous) clearMobileSession(previous)
  if (next != null) expired = false
}, { flush: 'sync' })
window.addEventListener('wms-auth-expired', () => { expired = true; store?.suspend(); touchMobile() }, { capture: true })

export function pendingMobileTasks() {
  return mobileStore()?.tasks().filter(task => ['submitting', 'uncertain'].includes(task.status)) || []
}
