import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8501',
      '/health': 'http://localhost:8501',
    },
  },
  build: {
    outDir: '../ui-dist',
    emptyOutDir: true,
  },
})
