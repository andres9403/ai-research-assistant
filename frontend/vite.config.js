import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Requests to /api are proxied to the FastAPI backend during development.
// BACKEND_URL (from the repo-root .env or the shell) points it elsewhere when
// the backend runs on another port.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, fileURLToPath(new URL("..", import.meta.url)), "");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: { "/api": env.BACKEND_URL || "http://localhost:8000" },
    },
  };
});
