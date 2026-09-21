export function canAccess(role, resource = '') {
  // 前端权限只控制菜单、路由和按钮可见性；后端接口仍独立执行同一角色边界。
  if (!['admin', 'warehouse', 'workshop'].includes(role)) return false
  if (role === 'admin') return true
  if (['users', 'settings', 'logs'].includes(resource)) return false
  return role !== 'workshop' || !['material-receipts', 'material-outbounds', 'finished-receipts', 'finished-outbounds', 'issues'].includes(resource)
}
export const canIssue = role => ['admin', 'warehouse'].includes(role)
// 车间可以补料、退料和登记实际产量，但不能正常领料或正式完工入库。
export const canAdjustProduction = role => ['admin', 'warehouse', 'workshop'].includes(role)
