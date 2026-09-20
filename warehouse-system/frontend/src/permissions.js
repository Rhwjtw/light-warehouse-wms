export function canAccess(role, resource = '') {
  if (!['admin', 'warehouse', 'workshop'].includes(role)) return false
  if (role === 'admin') return true
  if (['users', 'settings', 'logs'].includes(resource)) return false
  return role !== 'workshop' || !['material-receipts', 'material-outbounds', 'finished-receipts', 'finished-outbounds', 'issues'].includes(resource)
}
export const canIssue = role => ['admin', 'warehouse'].includes(role)
export const canAdjustProduction = role => ['admin', 'warehouse', 'workshop'].includes(role)
