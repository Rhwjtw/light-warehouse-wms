// 已登录用户可查看全部页面；角色仅决定哪些业务动作可提交。
export const rolePermissions = {
  admin: ['*'],
  warehouse: ['material.archive.write', 'material.inventory.write', 'finished.archive.write', 'finished.inventory.write', 'supplier.write', 'location.write', 'stock.reversal.write'],
  workshop_director: ['bom.write', 'production.order.write', 'production.manage', 'workshop.pick', 'workshop.replenish', 'workshop.return'],
  workshop: ['workshop.pick', 'workshop.replenish', 'workshop.return'],
  accountant: ['material.archive.write', 'finished.archive.write', 'bom.write', 'production.order.write', 'supplier.write'],
}

export const canAccess = role => Object.hasOwn(rolePermissions, role)
export const canOperate = (role, permission) => {
  const granted = rolePermissions[role] || []
  return granted.includes('*') || granted.includes(permission)
}
