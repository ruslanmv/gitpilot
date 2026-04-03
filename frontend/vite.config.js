// frontend/vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

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
          "/api": "http://localhost:8000",
          "/ws": {
            target: "ws://localhost:8000",
            ws: true,
          },
        },
  },
});