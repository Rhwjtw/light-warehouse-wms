import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('/node_modules/')) return
          if (id.includes('/node_modules/@zxing/')) return 'qr-decoder'
          if (id.includes('/node_modules/qrcode/') || id.includes('/node_modules/dijkstrajs/') || id.includes('/node_modules/pngjs/')) return 'qr-generator'
          if (id.includes('/node_modules/vue/') || id.includes('/node_modules/@vue/') || id.includes('/node_modules/vue-router/')) return 'vue-vendor'
          if (id.includes('/node_modules/element-plus/') || id.includes('/node_modules/@element-plus/')) return 'element-plus'
          return 'vendor'
        },
      },
    },
  },
})
