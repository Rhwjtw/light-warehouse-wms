import { quantityText, quantityUnits } from '../quantity.js'
import { receiptFields, receiptKind, receiptSnapshot, receiptSuccess, verifyReceipt } from './receipt.js'

export const taskLabels = { draft: '草稿', submitting: '提交中', uncertain: '待核实', succeeded: '已成功', rejected: '明确拒绝' }
export const lockedTask = task => ['submitting', 'uncertain', 'succeeded'].includes(task?.status)
const copy = value => JSON.parse(JSON.stringify(value))
const fieldsOnly = value => ({ quantity: String(value.quantity || '').slice(0, 80), location: String(value.location || '').slice(0, 80), notes: String(value.notes || '').slice(0, 300) })
const dictionary = value => value && typeof value === 'object' && !Array.isArray(value)
const allowedFields = (kind, value) => kind === receiptKind ? receiptFields(value) : fieldsOnly(value)
function validTask(id, task) {
  if (!dictionary(task) || task.id !== id || !/^[\w-]{1,80}$/.test(id) || !Object.hasOwn(taskLabels, task.status) || typeof task.record !== 'string' || typeof task.kind !== 'string' || !dictionary(task.fields)) return false
  const keys = Object.keys(allowedFields(task.kind, {}))
  if (!keys.every(field => typeof task.fields[field] === 'string') || Object.keys(task.fields).some(field => !keys.includes(field))) return false
  if (['submitting', 'uncertain', 'succeeded', 'rejected'].includes(task.status)) {
    if (typeof task.requestId !== 'string' || !dictionary(task.snapshot) || task.snapshot.record !== task.record) return false
    try { if (quantityUnits(task.snapshot.quantity) <= 0n) return false } catch { return false }
    if (task.kind === receiptKind) {
      try { receiptSnapshot(task.snapshot) } catch { return false }
      if (Object.keys(task.snapshot).some(field => field !== 'record' && !keys.includes(field))) return false
      if (task.status === 'succeeded' && (!dictionary(task.result) || !Number.isSafeInteger(task.result.id) || !Number.isSafeInteger(task.result.batchId))) return false
    }
  }
  return true
}

// 一个标签页的会话存储；只保存允许的作业字段，不接收密码、令牌或服务端余额。
export function createTaskStore({ storage, instance, user, newId, now = () => new Date().toISOString() }) {
  const key = `wms.mobile.v1:${JSON.stringify([instance, String(user)])}`
  let data = { tasks: {}, lists: {} }, healthy = true, warning = ''
  function unavailable() { healthy = false; warning = '草稿存储不可用或记录损坏。本页输入暂存，不能提交；请先保留输入并核对记录。' }
  try {
    const saved = storage.getItem(key)
    if (saved) {
      const parsed = JSON.parse(saved)
      if (!dictionary(parsed.tasks) || !dictionary(parsed.lists) || Object.entries(parsed.tasks).some(([id, task]) => !validTask(id, task))) throw new Error('invalid')
      data = parsed
      for (const task of Object.values(data.tasks)) if (task.status === 'submitting') { task.status = 'uncertain'; task.reason = '页面恢复时原请求尚未确认，请先核对。' }
    }
  } catch { unavailable() }
  function persist() {
    if (!healthy) return false
    try { storage.setItem(key, JSON.stringify(data)); return true } catch { unavailable(); return false }
  }
  persist()
  const find = id => Object.hasOwn(data.tasks, id) ? data.tasks[id] : null
  function editable(id) {
    const task = find(id)
    if (!task || lockedTask(task)) throw new Error('该作业不可再次编辑或提交')
    return task
  }
  return {
    get healthy() { return healthy }, get warning() { return warning },
    tasks: () => copy(Object.values(data.tasks)), get: id => find(id) ? copy(find(id)) : null,
    create(kind, record, origin) {
      const existing = Object.values(data.tasks).find(task => task.kind === kind && task.record === record && task.status !== 'succeeded')
      if (existing) return copy(existing)
      const id = newId()
      data.tasks[id] = { id, kind, record, origin, status: 'draft', fields: allowedFields(kind, {}), requestId: null, snapshot: null, createdAt: now(), reason: '' }
      persist(); return copy(data.tasks[id])
    },
    edit(id, fields) {
      const task = editable(id), next = allowedFields(task.kind, fields)
      if (JSON.stringify(next) !== JSON.stringify(task.fields)) { task.requestId = null; task.snapshot = null; task.status = 'draft'; task.reason = '' }
      task.fields = next; persist(); return copy(task)
    },
    begin(id, { ready, allowed }) {
      const task = editable(id)
      if (!ready || !allowed) throw new Error('请先核实登录、权限及当前记录')
      if (!healthy) throw new Error(warning)
      const quantity = quantityUnits(task.fields.quantity)
      if (quantity <= 0n || quantity > 99999999999999999999n) throw new Error('数量必须大于零且不超过十四位整数、六位小数')
      if (task.kind !== receiptKind && !task.fields.location.trim()) throw new Error('请填写演练库位')
      const normalized = task.kind === receiptKind ? receiptSnapshot(task.fields) : { ...task.fields, quantity: quantityText(quantity) }
      task.requestId ||= newId()
      task.snapshot = { record: task.record, ...normalized }
      task.status = 'submitting'; task.submittedAt = now(); task.reason = ''
      if (!persist()) { task.status = 'draft'; throw new Error(warning) }
      return copy(task)
    },
    settle(id, status, reason = '', result) {
      const task = find(id)
      // 迟到的明确成功仍可关闭待核实；拒绝只接受正在处理的明确响应。
      if (!task || !['submitting', 'uncertain'].includes(task.status) || !['succeeded', 'uncertain', 'rejected'].includes(status) || (task.status === 'uncertain' && status === 'rejected')) return false
      const success = status === 'succeeded' && task.kind === receiptKind ? receiptSuccess(task, result) : null
      task.status = status; task.reason = reason; task.finishedAt = now()
      if (status === 'succeeded') { task.fields = allowedFields(task.kind, {}); task.result = success || { label: '演练成功（未记账）', requestId: task.requestId } }
      persist(); return true
    },
    bindReceipt(id, row) {
      const task = find(id)
      if (task?.kind !== receiptKind || task.status !== 'succeeded') throw new Error('尚无明确成功的入库请求')
      verifyReceipt(task, row); task.result.movementId = row.receipt_movement_id
      persist(); return copy(task)
    },
    suspend() {
      for (const task of Object.values(data.tasks)) if (task.status === 'submitting') { task.status = 'uncertain'; task.reason = '离开或切后台，结果待核实；不代表服务器操作已取消。' }
      persist()
    },
    saveList(path, value) { data.lists[path] = { top: Math.max(0, Number(value.top) || 0), anchor: String(value.anchor || '').slice(0, 80) }; persist() },
    list: path => copy(Object.hasOwn(data.lists, path) ? data.lists[path] : { top: 0, anchor: '' }),
    clear() { data = { tasks: {}, lists: {} }; try { storage.removeItem(key) } catch { unavailable() } },
  }
}
