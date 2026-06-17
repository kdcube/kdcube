import { defineConfig } from 'tsup'

export default defineConfig({
  entry: {
    index: 'src/index.ts',
    'chat/index': 'src/chat/index.tsx',
    'canvas/index': 'src/canvas/index.ts',
  },
  format: ['esm'],
  dts: true,
  clean: true,
  sourcemap: true,
  treeshake: true,
  external: [
    'react',
    'react-dom',
    'react-redux',
    'lucide-react',
    '@kdcube/components-core',
    'react-markdown',
    'remark-gfm',
    'remark-breaks',
  ],
})
