import axios from 'axios'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 10000,
})

api.interceptors.response.use(
  response => response.data,
  error => Promise.reject(new Error(error.response?.data?.message || '无法连接服务，请检查后端是否启动')),
)
