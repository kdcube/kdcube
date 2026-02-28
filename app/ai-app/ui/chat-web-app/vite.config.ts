import {defineConfig, loadEnv} from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from "@tailwindcss/vite";
import basicSsl from '@vitejs/plugin-basic-ssl'

export default defineConfig(({mode}) => {

    // @ts-expect-error because reasons
    const env = loadEnv(mode, process.cwd(), '')

    const apiBase = env.VITE_APP_API_BASE ?? 'http://localhost:8010/'
    const integrationsApiBase = env.VITE_APP_INTEGRATIONS_API_BASE ?? 'http://localhost:8020/'

    return {
        plugins: [
            react(),
            tailwindcss(),
            basicSsl()
        ],
        resolve: {
            dedupe: ["react", "react-dom"],
        },
        envPrefix: ["VITE_", "CHAT_WEB_APP_"],
        server: {
            https: env.VITE_HTTPS === 'true',

            proxy: {
                '^/api/integrations/.*': {
                    target: integrationsApiBase,
                },
                '^/admin/integrations/.*': {
                    target: integrationsApiBase,
                },
                '^/api/.*': {
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
