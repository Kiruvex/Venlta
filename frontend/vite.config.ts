import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';

export default defineConfig({
  plugins: [preact()],
  server: {
    port: 5173,
  },
  build: {
    outDir: 'dist',
    // Ensure assets use relative paths so Qt can load them from local file://
    base: './',
  },
});
