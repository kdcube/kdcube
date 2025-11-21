import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const PORT = Number(process.env.VITE_DEV_PORT ?? 5175);
// https://vite.dev/config/
export default defineConfig({
    plugins: [react()],
    server: { port: PORT, strictPort: true }
})
