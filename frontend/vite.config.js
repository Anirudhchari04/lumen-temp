import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: {
    '/auth': 'http://localhost:8000',
    '/chat': 'http://localhost:8000',
    '/lumen': 'http://localhost:8000',
    '/agents': 'http://localhost:8000',
    '/events': 'http://localhost:8000',
  }},
  build: { outDir: 'dist' },
})
