import { qrText, readQrObject } from './qr.js'

export async function makeLabel(api, type, id, movementId, instance) {
  const text = qrText(type, { id, receipt_movement_id: movementId }, instance)
  const row = await readQrObject(api, text, type, instance)
  const { default: QRCode } = await import('qrcode')
  const image = await QRCode.toDataURL(text, { errorCorrectionLevel: 'M', margin: 4, scale: 8, color: { dark: '#000000', light: '#ffffff' } })
  // Human-readable fields are a print-time snapshot, never authority for balance or status.
  const lines = type === 'receipt'
    ? [row.material_name, row.material_code, `批号 ${row.batch_no}`, `供应商 ${row.supplier_name}`, `生产 ${row.production_date}`, `到期 ${row.expiry_date}`, `单位 ${row.unit}`, row.document_no, `单ID ${row.id} / 明细 ${row.receipt_movement_id}`]
    : [row.name, `库位ID ${row.id}`]
  return { key: text, type, id: String(row.id), movementId: type === 'receipt' ? String(row.receipt_movement_id) : '', text, image, lines, instance }
}
