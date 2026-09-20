import test from 'node:test'
import assert from 'node:assert/strict'
import { postingRequest } from '../src/postingRequest.js'

test('a lost response retries the same posting; edited content gets a new key', () => {
  const state = {}
  let sequence = 0
  const newId = () => String(++sequence)
  const payload = { order_material_id: 1, unit: 'kg', allocations: [{ batch_id: 2, quantity: '0.1' }] }
  const first = postingRequest(state, payload, newId)
  assert.deepEqual(postingRequest(state, structuredClone(payload), newId), first)
  assert.equal(sequence, 1)
  payload.allocations[0].quantity = '0.2'
  assert.notEqual(postingRequest(state, payload, newId).request_id, first.request_id)
  assert.equal(sequence, 2)
})
