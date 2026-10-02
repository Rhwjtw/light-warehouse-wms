// after_quantity is the balance of the posted allocations, not the whole product.
export function finishedPostingBalanceLabel(result) {
  const allocations = result?.allocations || []
  if (allocations.length > 1) return '本次所选库存记账后余额合计'
  return allocations[0]?.stock_scope === 'batch' ? '所选真实批次记账后余额' : '历史无批次归属库存记账后余额'
}
