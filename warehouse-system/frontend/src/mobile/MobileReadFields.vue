<script setup>
import { computed } from 'vue'
import { quantityText, scopeLabel, kindLabels, receiptStatuses } from './readQueries'
const props = defineProps({ record: { type: Object, required: true } })
const labels = { id: '记录ID', document_id: '单据ID', document_no: '单据号', receipt_movement_id: '入库明细ID', batch_id: '批次ID', material_batch_id: '原料批次ID', finished_batch_id: '成品库存ID', material_code: '原料编码', product_code: '成品编码', material_name: '原料', product_name: '成品', item_name: '货物', name: '名称', code: '编码', supplier_name: '供应商', location_id: '库位ID', location_name: '库位', batch_no: '批号', production_date: '生产日', expiry_date: '到期日', received_at: '入库时间', created_at: '记录时间', business_date: '业务日期', business_type: '业务类型', reference_no: '关联单号', received_quantity: '累计入库', shelved_quantity: '已上架', pending_quantity: '待上架', remaining_quantity: '当前余额', quantity: '数量', before_quantity: '变动前', change_quantity: '变动量', after_quantity: '变动后', input_quantity: '原始输入', unit: '库存单位', input_unit: '输入单位', source_location_name: '来源库位', target_location_name: '目标库位', operator_name: '操作人', counted_by_name: '盘点人', counted_at: '盘点时间', book_quantity: '账面量', actual_quantity: '实盘量', difference: '差异', reason: '原因', notes: '备注', reversal_reason: '冲销原因', reverses_document_id: '原单ID', reversed_by_document_id: '冲销单ID', production_order_no: '生产单号', kind: '业务', status: '状态', is_active: '档案状态', expired: '有效期状态', stock_scope: '库存归属', material_outbound_purpose: '出库用途' }
function display(key, value) {
  if (key.endsWith('_quantity') || ['quantity', 'difference'].includes(key)) return quantityText(value, key === 'input_quantity' ? props.record.input_unit : props.record.unit)
  if (key === 'stock_scope') return scopeLabel(value, props.record.batch_no)
  if (key === 'is_active') return value ? '启用' : '已停用'
  if (key === 'expired') return value ? '已过期' : '未过期（以服务端业务日判断）'
  if (key === 'kind') return kindLabels[value] || value
  if (key === 'status') return receiptStatuses[value] || { normal: '原单有效', reversal: '冲销单' }[value] || value
  if (key === 'material_outbound_purpose') return { normal: '普通出库', supplier_return: '供应商退货', scrap: '报废' }[value] || value
  return value
}
const fields = computed(() => Object.entries(labels).filter(([key]) => props.record[key] !== null && props.record[key] !== undefined && props.record[key] !== '').map(([key, label]) => ({ key, label: key === 'received_quantity' && props.record.receipt_movement_id ? '本笔入库' : label, value: display(key, props.record[key]) })))
</script>
<template><dl class="mobile-details mobile-read-fields"><div v-for="field in fields" :key="field.key"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl></template>
