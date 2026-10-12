import { quantityUnits } from '../quantity.js'

// Deployment-owned identity: never infer it from URL, database IDs or local storage.
export function qrInstance(value = import.meta.env?.VITE_WMS_QR_INSTANCE) {
  if (typeof value !== 'string' || !/^[a-f0-9]{32}$/.test(value)) throw new Error('尚未配置有效的标签实例标识，请联系管理员；可继续手动上架')
  return value
}
export function qrId(value) {
  const text = String(value)
  if (!/^[1-9]\d{0,9}$/.test(text) || BigInt(text) > 2147483647n) throw new Error('标签对象ID无效')
  return text
}
export function parseQr(text, expectedType, instance) {
  qrInstance(instance)
  if (typeof text !== 'string' || text.length > 100 || /\s/.test(text)) throw new Error('不是有效的轻仓标签')
  const parts = text.split(':')
  if (parts[0] !== 'wmsqr') throw new Error('不是轻仓标签；不会打开外部链接')
  if (parts[1] !== 'v1') throw new Error('标签版本不受支持')
  qrInstance(parts[2])
  if (parts[2] !== instance) throw new Error('标签属于另一套数据，不能在当前系统使用')
  if (!['receipt', 'location'].includes(parts[3])) throw new Error('标签类型不受支持')
  if (parts[3] !== expectedType) throw new Error(expectedType === 'receipt' ? '请扫描货物入库明细标签' : '请扫描库位标签')
  if (parts.length !== (expectedType === 'receipt' ? 6 : 5)) throw new Error('标签字段数量无效')
  return { type: expectedType, instance, id: qrId(parts[4]), ...(expectedType === 'receipt' ? { movementId: qrId(parts[5]) } : {}) }
}
export function qrText(type, row, instance) {
  qrInstance(instance)
  if (type === 'receipt') return `wmsqr:v1:${instance}:receipt:${qrId(row.id)}:${qrId(row.receipt_movement_id)}`
  if (type === 'location') return `wmsqr:v1:${instance}:location:${qrId(row.id)}`
  throw new Error('标签类型不受支持')
}
export function verifyQrRow(label, row, open = true) {
  if (!row || String(row.id) !== label.id) throw new Error('标签与读取对象身份不符')
  if (label.type === 'receipt') {
    if (String(row.receipt_movement_id) !== label.movementId) throw new Error('入库单与明细不匹配，不按批号猜记录')
    qrId(row.batch_id); qrId(row.material_id); qrId(row.supplier_id)
    if (!row.unit || !row.document_no) throw new Error('具体入库记录不完整')
    if (open && (!['pending', 'partial'].includes(row.status) || quantityUnits(row.pending_quantity) <= 0n)) throw new Error('该明细已全部上架或已冲销，不能继续')
  } else if (row.is_active !== true) throw new Error('库位已停用，不能继续')
  return row
}
export async function readQrObject(api, text, type, instance) {
  const label = parseQr(text, type, instance)
  const row = verifyQrRow(label, (await api.get(`/${type === 'receipt' ? 'material-receipts' : 'locations'}/${label.id}`)).data)
  if (type === 'receipt') {
    const [material, supplier] = (await Promise.all([api.get(`/materials/${row.material_id}`), api.get(`/suppliers/${row.supplier_id}`)])).map(r => r.data)
    if (String(material.id) !== String(row.material_id) || String(supplier.id) !== String(row.supplier_id) || !material.is_active || !supplier.is_active || material.unit !== row.unit) throw new Error('原料、供应商已失效或单位已变化')
  }
  return row
}
