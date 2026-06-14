import { defineConfig, Plugin } from 'vite';
import preact from '@preact/preset-vite';

// Remove crossorigin attributes from built HTML to prevent file:// CORS issues
// in Qt WebEngine. When loaded via file:// protocol, crossorigin attribute
// causes Chromium to reject module/CSS loads.
function removeCrossorigin(): Plugin {
  return {
    name: 'remove-crossorigin',
    transformIndexHtml(html) {
      return html.replace(/\s+crossorigin/g, '');
    },
  };
}

export default defineConfig({
  plugins: [preact(), removeCrossorigin()],
  // CRITICAL: base must be top-level (not inside build) in Vite 5.
  // './' generates relative asset paths (./assets/...) so Qt WebEngine
  // can resolve them from file:// URLs. Without this, Vite defaults to '/'
  // and produces absolute paths (/assets/...) which resolve to the root
  // of the filesystem under file:// protocol, causing a blank UI.
  base: './',
  server: {
    port: 5173,
  },
  build: {
    outDir: 'dist',
  },
});
