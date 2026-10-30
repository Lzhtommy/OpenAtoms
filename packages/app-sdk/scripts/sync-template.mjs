// Copies the SDK source into the app template so generated apps can use it without a package install.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dest = resolve(here, "../../../templates/react-shadcn-vite/src/lib/oa.ts");
mkdirSync(dirname(dest), { recursive: true });
copyFileSync(resolve(here, "../src/index.ts"), dest);
console.log("synced app-sdk ->", dest);
