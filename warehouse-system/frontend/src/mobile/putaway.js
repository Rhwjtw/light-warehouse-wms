import { quantityText, quantityUnits } from '../quantity.js'
import { receiptOrigin } from './receipt.js'

export const putawayKind = 'material-putaway'
const limits = { receiptId: 10, movementId: 10, sourceBatchId: 10, locationId: 10, unit: 16, quantity: 80, notes: 2000 }
export function putawayFields(value = {}) {
  return Object.fromEntries(Object.entries(limits).map(([key, limit]) => [key, String(value[key] ?? '').slice(0, limit)]))
}
export const putawayRecord = fields => `${fields.receiptId}:${fields.movementId}`
export const putawayTaskPath = (task, step = 'form') => `/m/jobs/putaway/${task.id}/${step}`
export const putawayOrigin = receiptOrigin
export function verifyPutawayReceipt(fields, row, open = false) {
  if (!['receiptId', 'movementId', 'sourceBatchId'].every(key => /^[1-9]\d{0,9}$/.test(fields[key])) || !fields.unit.trim()) throw new Error('具体入库身份无效')
  if (String(row.id) !== fields.receiptId || String(row.receipt_movement_id) !== fields.movementId || String(row.batch_id) !== fields.sourceBatchId || row.unit !== fields.unit) throw new Error('入库单与具体明细身份不符，请重新选择')
  if (open && (row.status === 'reversed' || quantityUnits(row.pending_quantity) <= 0n)) throw new Error('本记录已冲销或已全部上架，不能继续')
  return row
}
export function putawaySnapshot(fields) {
  const data = putawayFields(fields)
  if (!['receiptId', 'movementId', 'sourceBatchId', 'locationId'].every(key => /^[1-9]\d{0,9}$/.test(data[key])) || !data.unit.trim()) throw new Error('请选择具体入库明细与库位')
  const quantity = quantityUnits(data.quantity)
  if (quantity <= 0n || quantity > 99999999999999999999n) throw new Error('数量必须大于零且不超过十四位整数、六位小数')
  return { ...data, quantity: quantityText(quantity), notes: data.notes.trim() }
}
export function putawayPayload(task) {
  const s = task.snapshot
  return { request_id: task.requestId, receipt_movement_id: Number(s.movementId), location_id: Number(s.locationId), quantity: s.quantity, notes: s.notes || null }
}
export function putawaySuccess(task, result) {
  if (!result || result.request_id !== task.requestId || result.kind !== 'putaway' || !Number.isSafeInteger(result.id) || result.id <= 0 || !Number.isSafeInteger(result.target_batch_id) || result.target_batch_id <= 0 || String(result.source_batch_id) !== task.snapshot.sourceBatchId || typeof result.document_no !== 'string' || quantityUnits(result.quantity) !== quantityUnits(task.snapshot.quantity)) throw new Error('成功响应无法绑定原上架请求，结果待核实')
  return { id: result.id, documentNo: result.document_no, targetBatchId: result.target_batch_id }
}
export async function readPutawayResult(api, task) {
  const s = task.snapshot, r = task.result
  const [receipt, document, target] = (await Promise.all([api.get(`/material-receipts/${s.receiptId}`), api.get(`/stock-documents/${r.id}`), api.get(`/material-location-stock/${r.targetBatchId}`)])).map(response => response.data)
  verifyPutawayReceipt(s, receipt)
  const allocation = receipt.allocations.find(row => row.document_id === r.id)
  const flows = document.transactions
  if (document.id !== r.id || document.kind !== 'putaway' || document.document_no !== r.documentNo || !allocation || quantityUnits(allocation.quantity) !== quantityUnits(s.quantity) || !Array.isArray(flows) || flows.length !== 2 || !flows.some(row => String(row.material_batch_id) === s.sourceBatchId && quantityUnits(row.change_quantity) === -quantityUnits(s.quantity)) || !flows.some(row => row.material_batch_id === r.targetBatchId && quantityUnits(row.change_quantity) === quantityUnits(s.quantity)) || target.id !== r.targetBatchId || String(target.location_id) !== s.locationId || target.material_id !== receipt.material_id || target.unit !== s.unit) throw new Error('本笔上架单、分配或库位库存身份不符，暂停结果关联')
  const stocks = []
  for (let page = 1; ; page++) {
    const data = (await api.get('/material-location-stock', { params: { material_id: receipt.material_id, page, page_size: 100 } })).data
    stocks.push(...data.items)
    if (page * 100 >= data.total) break
  }
  return { receipt, document, target, stocks }
}
