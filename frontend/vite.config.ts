import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/klines": { target: "http://localhost:8000" },
      "/liquidations": { target: "http://localhost:8000" },
      "/liquidation-events": { target: "http://localhost:8000" },
      "/liq-post-event": { target: "http://localhost:8000" },
      "/polymarket": { target: "http://localhost:8000" },
      "/simulation": { target: "http://localhost:8000" },
      "/live": { target: "http://localhost:8000" },
    },
  },
});
