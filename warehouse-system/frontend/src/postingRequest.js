// 响应丢失时，同一表单内容重试必须沿用请求标识，避免再次扣减库存；内容变化后才生成新标识。
export function postingRequest(state, payload, newId) {
  const content = JSON.stringify(payload)
  if (content !== state.requestContent) {
    state.requestKey = newId()
    state.requestContent = content
  }
  return { request_id: state.requestKey, ...payload }
}
