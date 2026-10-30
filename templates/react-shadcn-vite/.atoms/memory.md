# Project memory

- Stack: Vite 5 + React 18 + TypeScript + Tailwind 3 + shadcn-style components (`src/components/ui`).
- Path alias `@/` → `src/`. Utility `cn()` in `src/lib/utils.ts`.
- Backend (OpenAtoms Cloud): `import { oa } from "@/lib/oa"` → `oa.entities.<Name>.list/create/update/delete`, `oa.auth`, `oa.storage`, `oa.ai.gentxt`.
- Verify with `pnpm build` before finishing.
