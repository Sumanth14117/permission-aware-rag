import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    proxy: {
      "/auth": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
      "/documents": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
      "/qa": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    clearMocks: true,
    restoreMocks: true,
  },
});
