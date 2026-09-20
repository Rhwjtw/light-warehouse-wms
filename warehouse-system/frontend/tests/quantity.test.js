import test from 'node:test'
import assert from 'node:assert/strict'
import { quantityUnits, quantityText } from '../src/quantity.js'

test('return balance retains exact decimals and large inventory quantities', () => {
  assert.equal(quantityText(quantityUnits('0.3') - quantityUnits('0.2')), '0.1')
  assert.equal(quantityText(quantityUnits('20') + quantityUnits('1.2') - quantityUnits('0.5')), '20.7')
  assert.equal(quantityText(quantityUnits('99999999999999.999999') - quantityUnits('0.000001')), '99999999999999.999998')
  assert.equal(quantityText(quantityUnits('0.1') + quantityUnits('0.2') - quantityUnits('0.3')), '0')
})
test('invalid precision is rejected', () => {
  for (const value of ['NaN', 'Infinity', '0.0000001', '1e3', '']) assert.throws(() => quantityUnits(value))
})
