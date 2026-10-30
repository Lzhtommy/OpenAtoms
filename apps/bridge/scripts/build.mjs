import { build } from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const out = resolve(root, "dist/bridge.js");
await build({
  entryPoints: [resolve(root, "src/index.ts")],
  bundle: true,
  format: "iife",
  target: ["es2019"],
  minify: true,
  outfile: out,
  legalComments: "none",
});
const gatewayAsset = resolve(root, "../gateway/assets/bridge.js");
mkdirSync(dirname(gatewayAsset), { recursive: true });
copyFileSync(out, gatewayAsset);
console.log("built", out, "->", gatewayAsset);
