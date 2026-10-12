import { quantityText, quantityUnits } from '../quantity.js'

export const receiptKind = 'material-receipt'
const limits = { materialId: 10, supplierId: 10, unit: 16, batchNo: 80, productionDate: 10, expiryDate: 10, quantity: 80, notes: 2000 }
export function receiptFields(value = {}) {
  return Object.fromEntries(Object.entries(limits).map(([key, limit]) => [key, String(value[key] ?? '').slice(0, limit)]))
}
export function receiptSnapshot(fields) {
  const data = receiptFields(fields)
  if (!/^[1-9]\d{0,9}$/.test(data.materialId) || !/^[1-9]\d{0,9}$/.test(data.supplierId)) throw new Error('请选择原料和供应商')
  if (!data.unit.trim() || !data.batchNo.trim()) throw new Error('请填写批号，并重新读取原料单位')
  for (const key of ['productionDate', 'expiryDate']) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(data[key]) || !Number.isFinite(Date.parse(data[key])) || new Date(data[key]).toISOString().slice(0, 10) !== data[key]) throw new Error('请填写有效的生产日期和到期日期')
  }
  if (data.expiryDate < data.productionDate) throw new Error('到期日期不能早于生产日期')
  const quantity = quantityUnits(data.quantity)
  if (quantity <= 0n || quantity > 99999999999999999999n) throw new Error('数量必须大于零且不超过十四位整数、六位小数')
  return { ...data, unit: data.unit.trim(), batchNo: data.batchNo.trim(), quantity: quantityText(quantity), notes: data.notes.trim() }
}
export function receiptPayload(task) {
  const s = task.snapshot
  return { request_id: task.requestId, material_id: Number(s.materialId), supplier_id: Number(s.supplierId), batch_no: s.batchNo, quantity: s.quantity, unit: s.unit, production_date: s.productionDate, expiry_date: s.expiryDate, notes: s.notes || null }
}
export function receiptSuccess(task, result) {
  if (!result || result.request_id !== task.requestId || result.kind !== 'material_receipt' || !Number.isSafeInteger(result.id) || result.id <= 0 || !Number.isSafeInteger(result.batch_id) || result.batch_id <= 0 || typeof result.document_no !== 'string' || result.unit !== task.snapshot.unit || quantityUnits(result.quantity) !== quantityUnits(task.snapshot.quantity)) throw new Error('成功响应无法绑定原请求，结果待核实')
  // 仅保存不可变结果身份，不保存服务端前后余额。
  return { id: result.id, batchId: result.batch_id, documentNo: result.document_no, movementId: null }
}
export function verifyReceipt(task, row) {
  const s = task.snapshot, r = task.result
  if (!r || row.id !== r.id || row.batch_id !== r.batchId || !Number.isSafeInteger(row.receipt_movement_id) || row.receipt_movement_id <= 0 || (r.movementId && row.receipt_movement_id !== r.movementId) || String(row.material_id) !== s.materialId || String(row.supplier_id) !== s.supplierId || row.batch_no !== s.batchNo || row.production_date !== s.productionDate || row.expiry_date !== s.expiryDate || row.unit !== s.unit || quantityUnits(row.received_quantity) !== quantityUnits(s.quantity)) throw new Error('入库详情身份不符，暂停结果关联，请核对本笔单据')
  return row
}
export function receiptOrigin(value) {
  if (typeof value !== 'string' || value.includes('\\')) return '/m/jobs'
  try { const u = new URL(value, 'https://wms.invalid'); return u.origin === 'https://wms.invalid' && ['/m', '/m/jobs', '/m/jobs/inbound', '/m/pending', '/m/records'].includes(u.pathname) ? u.pathname + u.search : '/m/jobs' } catch { return '/m/jobs' }
}
export function receiptTaskPath(task, step = 'form') { return `/m/jobs/receipt/${task.id}/${step}` }

export async function readReceiptStock(api, material) {
  // 汇总接口没有material_id参数；按编码远程搜索并分页寻找精确ID。
  for (let page = 1; ; page++) {
    const data = (await api.get('/material-stock', { params: { q: material.code, page, page_size: 100 } })).data
    const found = data.items.find(row => row.id === material.id)
    if (found) return found
    if (page * 100 >= data.total) throw new Error('未读取到该原料汇总，库存待刷新')
  }
}
