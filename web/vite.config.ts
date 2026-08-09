import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Tailwind v4 has no tailwind.config.js and no PostCSS step: the theme is CSS, in
  // src/index.css, and this plugin is the whole build integration.
  plugins: [react(), tailwindcss()],

  // MapLibre's tile-parsing worker is an ES module, and it is loaded through
  // `setWorkerUrl` in src/map/maplibreWorker.ts — see that file for why MapLibre cannot
  // find it on its own under a bundler. Without `format: 'es'` Vite emits the worker as
  // IIFE, its own `import` statements fail, and the basemap goes blank in the same
  // near-silent way.
  worker: { format: 'es' },
})
