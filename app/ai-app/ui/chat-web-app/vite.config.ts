import {defineConfig, loadEnv} from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({mode}) => {
    const env = loadEnv(mode, process.cwd(), '')
    const apiBase = env.VITE_APP_API_BASE ?? 'http://localhost:8010/'

    return {
        plugins: [
            react(),
            tailwindcss(),
        ],
        resolve: {
            dedupe: ["react", "react-dom"],
        },
        envPrefix: ["VITE_", "CHAT_WEB_APP_"],
        server: {
            proxy: {
                '^/api/.*': {
                    target: apiBase,
                },
                '^/integrations/.*': {
                    target: apiBase,
                },
                '^/profile': {
                    target: apiBase,
                },
                '^/admin/.*': {
                    target: apiBase,
                },
                '^/monitoring/.*': {
                    target: apiBase,
                },
                '^/socket.io': {
                    target: apiBase,
                },
                '^/sse/.*': {
                    target: apiBase,
                },
            }
        }
    }
})
