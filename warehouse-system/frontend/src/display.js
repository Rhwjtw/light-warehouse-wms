import { auth } from './auth'
export const kindNames = { material_receipt: '原材料入库', material_outbound: '原材料出库', issue: '生产领料', supplement: '生产补料', return: '生产退料', finished_receipt: '成品入库', finished_outbound: '成品出库' }
export const statusNames = { draft: '未开始', ready: '待领料', in_progress: '生产中', completed: '已完成', received: '已入库', cancelled: '已取消' }
export function dateTime(value, zone = auth.user?.business_timezone || 'Asia/Shanghai') {
  return value ? new Date(value).toLocaleString('zh-CN', { timeZone: zone, hour12: false }) : '—'
}
