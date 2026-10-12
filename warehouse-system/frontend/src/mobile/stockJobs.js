import { quantityText, quantityUnits } from '../quantity.js'
import { safeReadOrigin } from './readQueries.js'

export const stockKinds = ['material-outbound', 'finished-outbound', 'finished-receipt']
export const stockLabels = { 'material-outbound': '原料普通出库', 'finished-outbound': '成品出库', 'finished-receipt': '成品普通入库' }
export const stockPath = (task, step = 'form') => `/m/jobs/${task.kind}/${task.id}/${step}`
export const stockRecord = (kind, fields) => kind === 'material-outbound' ? `${fields.itemId}:${fields.batchId}` : fields.itemId
export function stockOrigin(value) {
  if (typeof value !== 'string' || value.includes('\\') || /[\u0000-\u001f]/.test(value)) return '/m/jobs'
  try { const u = new URL(value, 'https://wms.invalid'); return u.origin === 'https://wms.invalid' && ['/m', '/m/jobs', '/m/jobs/inbound', '/m/jobs/outbound'].includes(u.pathname) ? u.pathname + u.search : safeReadOrigin(value, '/m/jobs') } catch { return '/m/jobs' }
}
const limits = { itemId: 10, batchId: 10, locationId: 10, unit: 16, quantity: 80, businessDate: 10, referenceNo: 120, notes: 2000 }
const id = value => /^[1-9]\d{0,9}$/.test(String(value))
export function stockFields(value = {}) {
  const fields = Object.fromEntries(Object.entries(limits).map(([key, limit]) => [key, String(value[key] ?? '').slice(0, limit)]))
  fields.allocations = Array.isArray(value.allocations) ? value.allocations.slice(0, 100).map(row => ({ batchId: String(row.batchId ?? '').slice(0, 10), scope: String(row.scope ?? '').slice(0, 16), quantity: String(row.quantity ?? '').slice(0, 80) })) : []
  return fields
}
function positive(value) {
  const units = quantityUnits(value)
  if (units <= 0n || units > 99999999999999999999n) throw new Error('数量必须大于零且不超过十四位整数、六位小数')
  return quantityText(units)
}
export function stockSnapshot(kind, fields) {
  if (Array.isArray(fields.allocations) && fields.allocations.length > 100) throw new Error('原API每笔最多100条分配')
  const s = stockFields(fields)
  if (!stockKinds.includes(kind) || !id(s.itemId) || !s.unit.trim()) throw new Error('请重新选择具体货物并读取单位')
  s.quantity = positive(s.quantity); s.notes = s.notes.trim(); s.referenceNo = s.referenceNo.trim()
  if (kind === 'material-outbound') {
    if (!id(s.batchId) || !id(s.locationId) || s.allocations.length) throw new Error('请选择具体库位批次')
  } else {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s.businessDate) || !Number.isFinite(Date.parse(s.businessDate)) || new Date(s.businessDate).toISOString().slice(0, 10) !== s.businessDate) throw new Error('请填写有效业务日期')
    if (kind === 'finished-receipt' && s.allocations.length) throw new Error('普通入库不能指定生产批次')
    if (kind === 'finished-outbound') {
      if (!s.allocations.length || new Set(s.allocations.map(a => a.batchId)).size !== s.allocations.length) throw new Error('请明确选择批次且不可重复')
      s.allocations = s.allocations.map(a => {
        if (!id(a.batchId) || !['batch', 'aggregate'].includes(a.scope)) throw new Error('批次归属未确认，禁止自动转换')
        return { ...a, quantity: positive(a.quantity) }
      }).sort((a, b) => Number(a.batchId) - Number(b.batchId))
      if (s.allocations.reduce((sum, a) => sum + quantityUnits(a.quantity), 0n) !== quantityUnits(s.quantity)) throw new Error('各批次数量合计必须等于本次总量')
    }
  }
  return s
}
export function stockPayload(task) {
  const s = task.snapshot, common = { request_id: task.requestId, quantity: s.quantity, unit: s.unit, notes: s.notes || null }
  if (task.kind === 'material-outbound') return { ...common, material_id: Number(s.itemId), batch_id: Number(s.batchId), purpose: 'normal' }
  return { ...common, product_id: Number(s.itemId), business_date: s.businessDate, business_type: 'normal', reference_no: s.referenceNo || null, ...(task.kind === 'finished-outbound' ? { allocations: s.allocations.map(a => ({ batch_id: Number(a.batchId), quantity: a.quantity })) } : {}) }
}
export function stockSuccess(task, result) {
  const s = task.snapshot
  if (!result || result.request_id !== task.requestId || result.kind !== task.kind.replace('-', '_') || !Number.isSafeInteger(result.id) || result.id <= 0 || typeof result.document_no !== 'string' || !result.document_no || result.unit !== s.unit || quantityUnits(result.quantity) !== quantityUnits(s.quantity)) throw new Error('成功响应不能绑定原请求，结果待核实')
  if (task.kind === 'material-outbound') {
    if (String(result.batch_id) !== s.batchId || result.purpose !== 'normal') throw new Error('出库批次或用途不符')
  } else {
    if (String(result.product_id) !== s.itemId || result.business_type !== 'normal' || result.business_date !== s.businessDate || (result.reference_no || '') !== s.referenceNo || !Array.isArray(result.allocations)) throw new Error('成品单据身份不符')
    const actual = result.allocations.map(a => ({ batchId: String(a.batch_id), scope: a.stock_scope, quantity: quantityText(quantityUnits(a.quantity)) })).sort((a, b) => Number(a.batchId) - Number(b.batchId))
    if (task.kind === 'finished-outbound' ? JSON.stringify(actual) !== JSON.stringify(s.allocations) : actual.length !== 1 || actual[0].scope !== 'aggregate' || actual[0].quantity !== s.quantity || !id(actual[0].batchId)) throw new Error('本笔批次分配不符')
  }
  return { id: result.id, documentNo: result.document_no, batchIds: task.kind === 'material-outbound' ? [Number(s.batchId)] : result.allocations.map(a => a.batch_id) }
}
export async function readStockSelection(api, kind, fields) {
  const f = stockFields(fields)
  if (!id(f.itemId)) throw new Error('请先选择货物')
  if (kind === 'material-outbound') {
    if (!id(f.batchId)) throw new Error('请选择具体库位批次')
    const row = (await api.get(`/material-location-stock/${f.batchId}`)).data
    if (String(row.id) !== f.batchId || String(row.material_id) !== f.itemId || String(row.location_id) !== f.locationId || row.unit !== f.unit) throw new Error('具体库位批次身份或单位已变化')
    const [item, supplier, location] = (await Promise.all([api.get(`/materials/${f.itemId}`), api.get(`/suppliers/${row.supplier_id}`), api.get(`/locations/${f.locationId}`)])).map(r => r.data)
    if (String(item.id) !== f.itemId || item.unit !== f.unit || supplier.id !== row.supplier_id || String(location.id) !== f.locationId || ![item, supplier, location].every(r => r.is_active === true)) throw new Error('原料、供应商或库位已停用/单位已变化')
    if (row.expired) throw new Error('过期批次禁止普通出库')
    if (f.quantity && quantityUnits(f.quantity) > quantityUnits(row.remaining_quantity)) throw new Error('旧数量超过最新余额，请修改后重新核对')
    return { item, row, batches: [], balance: row.remaining_quantity }
  }
  const [item, stock, batches] = (await Promise.all([api.get(`/products/${f.itemId}`), api.get(`/finished-stock/${f.itemId}`), api.get(`/finished-stock/${f.itemId}/batches`)])).map(r => r.data)
  if (String(item.id) !== f.itemId || stock.id !== item.id || !item.is_active || item.unit !== f.unit || stock.unit !== f.unit || batches.some(b => b.unit !== f.unit || !['batch', 'aggregate'].includes(b.stock_scope))) throw new Error('成品已停用、单位变化或存在未确认归属库存')
  for (const a of f.allocations) {
    const row = batches.find(b => String(b.id) === a.batchId)
    if (!row || row.stock_scope !== a.scope) throw new Error('所选成品批次身份或归属已变化')
    if (a.quantity && quantityUnits(a.quantity) > quantityUnits(row.quantity)) throw new Error('旧批次数量超过最新余额，请修改后重新核对')
  }
  return { item, row: stock, batches, balance: stock.quantity }
}
export async function readStockResult(api, task) {
  const s = task.snapshot, r = task.result
  const document = (await api.get(`/stock-documents/${r.id}`)).data
  if (document.id !== r.id || document.document_no !== r.documentNo || document.kind !== task.kind.replace('-', '_') || !Array.isArray(document.transactions)) throw new Error('本笔单据或明细身份不符')
  if ((document.notes || '') !== s.notes || (task.kind === 'material-outbound' ? document.material_outbound_purpose !== 'normal' : document.business_type !== 'normal' || document.business_date !== s.businessDate || (document.reference_no || '') !== s.referenceNo)) throw new Error('本笔单据业务内容不符')
  let stocks, detail
  if (task.kind === 'material-outbound') {
    const flows = document.transactions
    if (flows.length !== 1 || String(flows[0].material_batch_id) !== s.batchId || flows[0].unit !== s.unit || quantityUnits(flows[0].change_quantity) !== -quantityUnits(s.quantity)) throw new Error('原料本笔流水身份不符')
    detail = (await api.get(`/material-location-stock/${s.batchId}`)).data
    if (String(detail.id) !== s.batchId || String(detail.material_id) !== s.itemId || String(detail.location_id) !== s.locationId || detail.unit !== s.unit) throw new Error('原料结果库位批次不符')
    const item = (await api.get(`/materials/${s.itemId}`)).data
    if (String(item.id) !== s.itemId || item.unit !== s.unit) throw new Error('原料结果档案不符')
    stocks = []
    for (let page = 1; ; page++) {
      const data = (await api.get('/material-location-stock', { params: { material_id: Number(s.itemId), page, page_size: 100 } })).data
      stocks.push(...data.items)
      if (page * 100 >= data.total) break
    }
  } else {
    detail = (await api.get(`/${task.kind === 'finished-receipt' ? 'finished-receipts' : 'finished-outbounds'}/${r.id}`)).data
    stockSuccess(task, detail)
    if (JSON.stringify(detail.allocations.map(a => a.batch_id)) !== JSON.stringify(r.batchIds) || document.transactions.length !== r.batchIds.length || !detail.allocations.every(a => document.transactions.some(t => t.finished_batch_id === a.batch_id && t.unit === s.unit && quantityUnits(t.change_quantity) === quantityUnits(a.quantity) * (task.kind === 'finished-receipt' ? 1n : -1n)))) throw new Error('成品本笔明细或流水不符')
    const item = (await api.get(`/products/${s.itemId}`)).data
    const stock = (await api.get(`/finished-stock/${s.itemId}`)).data
    stocks = (await api.get(`/finished-stock/${s.itemId}/batches`)).data
    if (String(item.id) !== s.itemId || stock.id !== item.id || item.unit !== s.unit || stock.unit !== s.unit || !r.batchIds.every(id => stocks.some(b => b.id === id && b.unit === s.unit))) throw new Error('相关成品库存身份不符')
    detail = { ...detail, stock }
  }
  return { document, detail, stocks }
}
