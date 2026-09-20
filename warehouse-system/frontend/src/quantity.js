// Six decimal places, matching SQL DECIMAL(20,6), without binary floating point.
export function quantityUnits(value) {
  const match = /^(-?)(\d+)(?:\.(\d{1,6}))?$/.exec(String(value))
  if (!match) throw new Error('数量必须是最多六位小数的十进制数')
  const units = BigInt(match[2]) * 1000000n + BigInt((match[3] || '').padEnd(6, '0'))
  return match[1] ? -units : units
}
export function quantityText(units) {
  const sign = units < 0n ? '-' : ''
  const digits = (units < 0n ? -units : units).toString().padStart(7, '0')
  const fraction = digits.slice(-6).replace(/0+$/, '')
  return sign + digits.slice(0, -6) + (fraction ? '.' + fraction : '')
}
