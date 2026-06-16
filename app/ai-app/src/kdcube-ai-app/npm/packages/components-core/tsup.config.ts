import { defineConfig } from 'tsup'

export default defineConfig({
  entry: {
    index: 'src/index.ts',
    'chat/index': 'src/chat/index.ts',
  },
  format: ['esm'],
  dts: true,
  clean: true,
  sourcemap: true,
  treeshake: true,
  // Keep runtime deps external so consumers dedupe a single copy.
  external: ['@reduxjs/toolkit', 'socket.io-client'],
})
