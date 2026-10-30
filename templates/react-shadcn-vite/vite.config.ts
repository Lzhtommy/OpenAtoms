import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { oaLocBabelPlugin } from "./vite-plugins/oa-loc";

const port = Number(process.env.PORT) || 5173;
const hmrClientPort = process.env.OA_HMR_CLIENT_PORT ? Number(process.env.OA_HMR_CLIENT_PORT) : undefined;

export default defineConfig(({ command }) => ({
  plugins: [
    // dev only: stamp JSX with data-oa-loc for OpenAtoms visual editing (never part of `vite build`)
    react(command === "serve" ? { babel: { plugins: [oaLocBabelPlugin(__dirname)] } } : {}),
  ],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    host: "0.0.0.0",
    port,
    strictPort: true,
    allowedHosts: true,
    hmr: hmrClientPort ? { clientPort: hmrClientPort } : undefined,
  },
  preview: { host: "0.0.0.0", port, allowedHosts: true },
}));
