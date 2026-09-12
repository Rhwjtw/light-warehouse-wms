import axios from 'axios'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 10000,
})

api.interceptors.response.use(
  response => response.data,
  error => {
    if (error.response?.status === 401 && !error.config?.url?.endsWith('/auth/login')) {
      sessionStorage.removeItem('wms_token')
      window.dispatchEvent(new Event('wms-auth-expired'))
    }
    const result = new Error(error.response?.data?.message || '无法连接服务，请检查后端是否启动')
    result.status = error.response?.status
    return Promise.reject(result)
  },
)
api.interceptors.request.use(config => {
  const token = sessionStorage.getItem('wms_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
