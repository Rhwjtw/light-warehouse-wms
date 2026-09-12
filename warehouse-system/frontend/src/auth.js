import { reactive } from 'vue'
import { api } from './api'
export const auth = reactive({ user: null })
export function clearAuth() { sessionStorage.removeItem('wms_token'); auth.user = null }
export async function login(username, password) {
  const result = await api.post('/auth/login', { username, password })
  sessionStorage.setItem('wms_token', result.data.access_token)
  auth.user = result.data.user
}
export async function restoreAuth() {
  if (!sessionStorage.getItem('wms_token')) return false
  if (!auth.user) auth.user = (await api.get('/auth/me')).data
  return true
}
export async function logout() { try { await api.post('/auth/logout') } finally { clearAuth() } }
window.addEventListener('wms-auth-expired', clearAuth)
