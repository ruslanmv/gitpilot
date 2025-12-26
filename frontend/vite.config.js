// frontend/vite.config.js (Fixed)
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 💡 FIX: This tells Vite to bind to 0.0.0.0,
    // making it reachable from the Windows host in WSL.
    port: 5173,
    host: true, // <--- Add this line
    // Only proxy API requests when NOT running in Vercel dev
    // (Vercel dev handles API routing to serverless functions)
    proxy: process.env.VERCEL ? undefined : {
      "/api": "http://localhost:8000"
    }
  }
});