// A lost response must not turn the same form retry into another stock posting.
export function postingRequest(state, payload, newId) {
  const content = JSON.stringify(payload)
  if (content !== state.requestContent) {
    state.requestKey = newId()
    state.requestContent = content
  }
  return { request_id: state.requestKey, ...payload }
}
