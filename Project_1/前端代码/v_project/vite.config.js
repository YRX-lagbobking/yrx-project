import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  //设置启动的额端口号--默认 5173
  server: {
    host:'localhost',
    port: 8080
  },
   resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src')
    }
  },
})
