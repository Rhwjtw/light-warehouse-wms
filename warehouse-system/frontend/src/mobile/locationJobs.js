import { quantityText, quantityUnits } from '../quantity.js'
import { stockOrigin } from './stockJobs.js'

export const locationKinds = ['material-transfer', 'material-count']
export const locationLabels = { 'material-transfer': '原料移库', 'material-count': '库位批次盘点' }
export const locationPath = (task, step = 'form') => `/m/jobs/${task.kind}/${task.id}/${step}`
export const locationOrigin = stockOrigin
export const locationRecord = fields => `${fields.itemId}:${fields.batchId}`
const id = value => /^[1-9]\d{0,9}$/.test(String(value))
const limits = { itemId: 10, batchId: 10, locationId: 10, unit: 16, quantity: 80, notes: 2000 }
export function locationFields(kind, value = {}) {
  const allowed = kind === 'material-transfer' ? { ...limits, targetLocationId: 10 } : limits
  return Object.fromEntries(Object.entries(allowed).map(([key, limit]) => [key, String(value[key] ?? '').slice(0, limit)]))
}
function quantity(value, zero = false) {
  const units = quantityUnits(value)
  if (units < 0n || (!zero && units === 0n) || units > 99999999999999999999n) throw new Error(`数量必须${zero ? '非负' : '大于零'}，最多十四位整数、六位小数`)
  return quantityText(units)
}
export function locationSnapshot(kind, fields, bookQuantity) {
  const s = locationFields(kind, fields)
  if (!locationKinds.includes(kind) || ![s.itemId, s.batchId, s.locationId].every(id) || !s.unit.trim()) throw new Error('请选择具体已上架库位批次并读取单位')
  s.quantity = quantity(s.quantity, kind === 'material-count'); s.notes = s.notes.trim()
  if (kind === 'material-transfer') {
    if (!id(s.targetLocationId) || s.targetLocationId === s.locationId) throw new Error('目标库位必须明确选择且与来源不同')
  } else {
    // 账面量只在核对内存及已提交的不可变请求快照中；从不写入可编辑草稿。
    s.bookQuantity = quantity(bookQuantity, true)
  }
  return s
}
export function locationPayload(task) {
  const s = task.snapshot
  return task.kind === 'material-transfer'
    ? { request_id: task.requestId, source_batch_id: Number(s.batchId), target_location_id: Number(s.targetLocationId), quantity: s.quantity, notes: s.notes || null }
    : { request_id: task.requestId, batch_id: Number(s.batchId), book_quantity: s.bookQuantity, actual_quantity: s.quantity, reason: s.notes || null }
}
export function locationSuccess(task, result) {
  const s = task.snapshot
  if (!result || result.request_id !== task.requestId || !Number.isSafeInteger(result.id) || result.id <= 0 || !result.document_no) throw new Error('成功响应未绑定本请求，结果待核实')
  if (task.kind === 'material-transfer') {
    if (result.kind !== 'transfer' || String(result.source_batch_id) !== s.batchId || !Number.isSafeInteger(result.target_batch_id) || result.target_batch_id <= 0 || result.target_batch_id === result.source_batch_id || quantityUnits(result.quantity) !== quantityUnits(s.quantity)) throw new Error('本笔移库批次或数量不符')
    return { id: result.id, documentNo: result.document_no, targetBatchId: result.target_batch_id, kind: result.kind }
  }
  const difference = quantityUnits(s.quantity) - quantityUnits(s.bookQuantity)
  const kind = difference > 0n ? 'count_gain' : difference < 0n ? 'count_loss' : 'count_match'
  if (result.kind !== kind || String(result.batch_id) !== s.batchId || quantityUnits(result.book_quantity) !== quantityUnits(s.bookQuantity) || quantityUnits(result.actual_quantity) !== quantityUnits(s.quantity) || quantityUnits(result.difference) !== difference) throw new Error('本笔盘点账面、实盘或差异不符')
  return { id: result.id, documentNo: result.document_no, kind }
}
export async function materialRows(api, itemId) {
  const rows = []
  for (let page = 1; ; page++) {
    const data = (await api.get('/material-batches', { params: { material_id: Number(itemId), page, page_size: 100 } })).data
    rows.push(...data.items)
    if (page * 100 >= data.total) return rows
  }
}
export async function readLocationSelection(api, kind, fields) {
  const f = locationFields(kind, fields)
  if (![f.itemId, f.batchId, f.locationId].every(id)) throw new Error('请先选择具体库位批次')
  const row = (await api.get(`/material-location-stock/${f.batchId}`)).data
  if (String(row.id) !== f.batchId || String(row.material_id) !== f.itemId || String(row.location_id) !== f.locationId || row.unit !== f.unit) throw new Error('来源库位批次身份或单位已变化')
  const [item, supplier, location] = (await Promise.all([api.get(`/materials/${f.itemId}`), api.get(`/suppliers/${row.supplier_id}`), api.get(`/locations/${f.locationId}`)])).map(r => r.data)
  if (String(item.id) !== f.itemId || item.unit !== f.unit || supplier.id !== row.supplier_id || String(location.id) !== f.locationId || ![item, supplier, location].every(r => r.is_active === true)) throw new Error('原料、供应商或来源库位已停用/单位变化')
  let target = null, targetBatch = null
  if (kind === 'material-transfer' && f.targetLocationId) {
    if (!id(f.targetLocationId) || f.targetLocationId === f.locationId) throw new Error('目标库位必须与来源不同')
    target = (await api.get(`/locations/${f.targetLocationId}`)).data
    if (String(target.id) !== f.targetLocationId || target.is_active !== true) throw new Error('目标库位已停用或身份变化')
    targetBatch = (await materialRows(api, f.itemId)).find(b => String(b.location_id) === f.targetLocationId && b.batch_no === row.batch_no && b.supplier_id === row.supplier_id) || null
    if (targetBatch && (targetBatch.unit !== row.unit || targetBatch.production_date !== row.production_date || targetBatch.expiry_date !== row.expiry_date)) throw new Error('目标库位同批号的单位或生产/到期日期不一致')
    if (f.quantity) {
      const amount = quantityUnits(f.quantity)
      if (amount > quantityUnits(row.remaining_quantity)) throw new Error('旧数量超过来源最新余额，请修改后重新核对')
      if (targetBatch && [targetBatch.remaining_quantity, targetBatch.received_quantity].some(q => quantityUnits(q) + amount > 99999999999999999999n)) throw new Error('目标库位批次数量超出范围')
    }
  }
  return { row, item, supplier, location, target, targetBatch, readAt: new Date().toISOString() }
}
export function locationConfirmation(task, selection) {
  return JSON.stringify({ id: task.id, snapshot: locationSnapshot(task.kind, task.fields, selection.row.remaining_quantity),
    source: [selection.row.batch_no, selection.row.supplier_id, selection.row.production_date, selection.row.expiry_date, selection.row.remaining_quantity],
    target: selection.targetBatch && [selection.targetBatch.id, selection.targetBatch.received_quantity, selection.targetBatch.remaining_quantity] })
}
export async function readLocationResult(api, task) {
  const s = task.snapshot, r = task.result, document = (await api.get(`/stock-documents/${r.id}`)).data
  if (document.id !== r.id || document.document_no !== r.documentNo || document.kind !== r.kind || (document.notes || '') !== s.notes || !Array.isArray(document.transactions)) throw new Error('本笔单据身份或内容不符')
  const source = (await api.get(`/material-location-stock/${s.batchId}`)).data
  if (String(source.id) !== s.batchId || String(source.material_id) !== s.itemId || String(source.location_id) !== s.locationId || source.unit !== s.unit) throw new Error('结果来源批次身份不符')
  let target = null, count = null
  if (task.kind === 'material-transfer') {
    target = (await api.get(`/material-location-stock/${r.targetBatchId}`)).data
    if (target.id !== r.targetBatchId || String(target.material_id) !== s.itemId || String(target.location_id) !== s.targetLocationId || target.unit !== s.unit || ['batch_no', 'supplier_id', 'production_date', 'expiry_date'].some(k => target[k] !== source[k])) throw new Error('结果目标批次归属不符')
    // 原单据流水只返回库位名称；稳定库位ID由上面的确切批次详情核验，不把名称当ID。
    if (document.transactions.length !== 2 || ![[Number(s.batchId), -1n], [r.targetBatchId, 1n]].every(([batch, sign]) => document.transactions.some(t => t.material_batch_id === batch && t.unit === s.unit && quantityUnits(t.change_quantity) === quantityUnits(s.quantity) * sign && quantityUnits(t.after_quantity) - quantityUnits(t.before_quantity) === quantityUnits(t.change_quantity)))) throw new Error('本笔移库流水或总量守恒不符')
  } else {
    for (let page = 1; ; page++) {
      const data = (await api.get('/material-counts', { params: { batch_id: Number(s.batchId), page, page_size: 100 } })).data
      count = data.items.find(c => c.document_id === r.id)
      if (count || page * 100 >= data.total) break
    }
    if (!count || String(count.batch_id) !== s.batchId || count.unit !== s.unit || (count.reason || '') !== s.notes || quantityUnits(count.book_quantity) !== quantityUnits(s.bookQuantity) || quantityUnits(count.actual_quantity) !== quantityUnits(s.quantity) || quantityUnits(count.difference) !== quantityUnits(s.quantity) - quantityUnits(s.bookQuantity)) throw new Error('本笔盘点明细不符或未读取')
    const diff = quantityUnits(count.difference), flows = document.transactions
    if (flows.length !== (diff === 0n ? 0 : 1) || flows.some(t => String(t.material_batch_id) !== s.batchId || t.unit !== s.unit || quantityUnits(t.before_quantity) !== quantityUnits(s.bookQuantity) || quantityUnits(t.change_quantity) !== diff || quantityUnits(t.after_quantity) !== quantityUnits(s.quantity))) throw new Error('本笔盘点流水不符')
  }
  const [item, location, supplier] = (await Promise.all([api.get(`/materials/${s.itemId}`), api.get(`/locations/${s.locationId}`), api.get(`/suppliers/${source.supplier_id}`)])).map(x => x.data)
  if (String(item.id) !== s.itemId || item.unit !== s.unit || String(location.id) !== s.locationId || supplier.id !== source.supplier_id) throw new Error('结果档案身份不符')
  if (target) { const location = (await api.get(`/locations/${s.targetLocationId}`)).data; if (String(location.id) !== s.targetLocationId) throw new Error('结果目标档案身份不符') }
  return { document, source, target, count, stocks: await materialRows(api, s.itemId) }
}
