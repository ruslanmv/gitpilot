// frontend/vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend does not always land on 8000: when that port is taken it starts on
// the next free one and reports which via GITPILOT_PORT (`make run` sets it).
// Hard-coding 8000 here would proxy the dev server at a backend that isn't
// there, so the target follows the backend instead.
const backendPort = process.env.GITPILOT_PORT || "8000";
const backendUrl = process.env.GITPILOT_BACKEND_URL || `http://localhost:${backendPort}`;

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(process.env.npm_package_version || "unknown"),
  },
  server: {
    port: 5173,
    host: true,
    // Only proxy API requests when NOT running in Vercel dev
    // (Vercel dev handles API routing to serverless functions)
    proxy: process.env.VERCEL
      ? undefined
      : {
          "/api": backendUrl,
          "/ws": {
            target: backendUrl.replace(/^http/, "ws"),
            ws: true,
          },
        },
  },
});