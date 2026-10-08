import { formatQuantity } from '../quantity.js'

// 全部查询走已有GET；分页交给服务端，不裁剪成前100条候选。
export const inventoryViews = {
  material: { label: '原料汇总', endpoint: '/material-stock', search: '原料名称 / 编码', quantity: 'remaining_quantity' },
  finished: { label: '成品库存', endpoint: '/finished-stock', search: '成品名称 / 编码', quantity: 'quantity' },
  batches: { label: '原料批次', endpoint: '/material-batches', search: '原料名称 / 编码 / 批号', quantity: 'remaining_quantity' },
  locations: { label: '库位库存', endpoint: '/material-location-stock', search: '批号', quantity: 'remaining_quantity' },
}
export const recordViews = {
  receipts: { label: '原料入库', endpoint: '/material-receipts', search: '原料 / 编码 / 批号 / 入库单号', quantity: 'received_quantity' },
  material: { label: '原料流水', endpoint: '/material-transactions', search: '原料 / 编码 / 批号 / 单据号 / 生产单', quantity: 'change_quantity' },
  finishedReceipts: { label: '成品入库', endpoint: '/finished-receipts', search: '成品 / 编码 / 单据号 / 外部单号', quantity: 'quantity' },
  finishedOutbounds: { label: '成品出库', endpoint: '/finished-outbounds', search: '成品 / 编码 / 单据号 / 外部单号', quantity: 'quantity' },
  finished: { label: '成品流水', endpoint: '/finished-transactions', search: '成品 / 编码 / 单据号 / 外部单号', quantity: 'change_quantity' },
  putaways: { label: '上架记录', endpoint: '/material-transfers', kind: 'putaway', quantity: 'quantity' },
  transfers: { label: '移库记录', endpoint: '/material-transfers', kind: 'transfer', quantity: 'quantity' },
  counts: { label: '盘点记录', endpoint: '/material-counts', quantity: 'difference' },
  reversals: { label: '冲销记录', endpoint: '/stock-documents/reversals', search: '单据号 / 原单号 / 原因', quantity: null },
}
export const receiptStatuses = { open: '待上架（未完毕）', pending: '未上架', partial: '部分上架', shelved: '已全部上架', reversed: '已冲销' }
export const kindLabels = { material_receipt: '原料入库', material_outbound: '原料出库', finished_receipt: '成品入库', finished_outbound: '成品出库', putaway: '上架', transfer: '移库', count_gain: '盘盈', count_loss: '盘亏', adjustment: '库存调整', reversal: '冲销', issue: '领料', supplement: '补料', return: '退料' }
export function positiveId(value) { return typeof value === 'string' && /^[1-9]\d{0,9}$/.test(value) ? value : undefined }
function supportedIds(endpoint) {
  return endpoint === '/material-location-stock' ? ['location_id', 'material_id'] : endpoint === '/material-batches' ? ['material_id'] : endpoint === '/material-transactions' ? ['material_id', 'batch_id', 'location_id'] : endpoint === '/material-counts' ? ['batch_id'] : ['/finished-transactions', '/finished-receipts', '/finished-outbounds'].includes(endpoint) ? ['product_id'] : []
}
export function readCriteria(query, views, defaultView) {
  const view = Object.hasOwn(views, query.view) ? query.view : defaultView
  const raw = Number(query.page)
  const ids = supportedIds(views[view].endpoint)
  return { view, q: typeof query.q === 'string' ? query.q.slice(0, 120) : '', page: Number.isSafeInteger(raw) && raw > 0 ? raw : 1,
    status: Object.hasOwn(receiptStatuses, query.status) ? query.status : undefined,
    ...Object.fromEntries(['location_id', 'material_id', 'batch_id', 'product_id'].map(field => [field, ids.includes(field) ? positiveId(query[field]) : undefined])) }
}
export function queryRequest(criteria, views) {
  const config = views[criteria.view], params = { page: criteria.page, page_size: 20 }
  if (config.search && criteria.q.trim()) params[criteria.view === 'locations' ? 'batch_no' : 'q'] = criteria.q.trim().slice(0, criteria.view === 'locations' ? 80 : 120)
  if (config.kind) params.kind = config.kind
  if (config.endpoint === '/material-receipts' && criteria.status) params.status = criteria.status
  for (const field of supportedIds(config.endpoint)) {
    if (criteria[field]) params[field] = criteria[field]
  }
  return { endpoint: config.endpoint, params }
}
export function rowKey(view, row) { return `s3-${view}-${row.receipt_movement_id || row.transaction_id || row.id}` }
export function rowTitle(row) { return row.material_name || row.product_name || row.name || row.document_no || `记录 ${row.id}` }
export function quantityText(value, unit) { return `${formatQuantity(value)} ${unit || ''}`.trim() }
export function scopeLabel(scope, batchNo) { return scope === 'batch' ? `真实批次：${batchNo}` : scope === 'multiple' ? `多批分配：${batchNo}` : '历史无批次归属库存' }
export function safeReadOrigin(value, fallback = '/m/inventory') {
  if (typeof value !== 'string' || value.includes('\\')) return fallback
  try {
    const url = new URL(value, 'https://wms.invalid')
    return url.origin === 'https://wms.invalid' && ['/m/inventory', '/m/records', '/m/pending', '/m/locations'].includes(url.pathname) ? url.pathname + url.search : fallback
  } catch { return fallback }
}
export function detailLocation(view, row, origin) {
  if (view === 'material') return { path: '/m/inventory', query: { view: 'batches', material_id: String(row.id), from: safeReadOrigin(origin) } }
  const kind = view === 'finished' && row.product_id === undefined ? 'finished' : ['batches', 'locations'].includes(view) ? 'batch' : view === 'receipts' ? 'receipt' : 'document'
  const id = kind === 'document' ? row.document_id || row.id : row.id
  return { path: `/m/read/${kind}/${id}`, query: { from: safeReadOrigin(origin) } }
}

// 每次新条件使旧请求失效；离开/认证失效后迟到响应不再发布。
export function readGate() {
  let generation = 0
  return { begin: () => ++generation, current: value => value === generation, cancel: () => { generation++ } }
}
