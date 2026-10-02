import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { finishedPostingBalanceLabel } from '../src/finishedPostingLabel.js'

test('posting balance explicitly distinguishes a real batch, aggregate and selected allocation sum', () => {
  assert.equal(finishedPostingBalanceLabel({ allocations: [{ stock_scope: 'batch' }] }), '所选真实批次记账后余额')
  assert.equal(finishedPostingBalanceLabel({ allocations: [{ stock_scope: 'aggregate' }] }), '历史无批次归属库存记账后余额')
  assert.equal(finishedPostingBalanceLabel({ allocations: [{ stock_scope: 'batch' }, { stock_scope: 'aggregate' }] }), '本次所选库存记账后余额合计')
  const component = readFileSync(new URL('../src/views/FinishedInventory.vue', import.meta.url), 'utf8')
  assert.match(component, /finishedPostingBalanceLabel\(success\)/)
})
