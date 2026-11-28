import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // 后端端口：默认 8089，本机被占用时可设 VITE_API_PORT 换端口
        target: `http://127.0.0.1:${process.env.VITE_API_PORT || '8089'}` ,
        changeOrigin: true,
      },
    },
  },
})
