"use client";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, errMsg } from "@/lib/api";

interface SecretRow {
  id: number;
  key: string;
  value: string;
  env: "dev" | "prod";
}

/** basePath: `/chats/:id/secrets` or `/workspaces/:wid/secrets` */
export function SecretsEditor({ basePath }: { basePath: string }) {
  const [rows, setRows] = useState<SecretRow[]>([]);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [env, setEnv] = useState<"dev" | "prod">("dev");
  const [busy, setBusy] = useState(false);
  const load = () =>
    api
      .get<SecretRow[]>(basePath)
      .then(setRows)
      .catch((e) => toast.error(errMsg(e)));
  useEffect(() => {
    load();
  }, [basePath]);
  return (
    <div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!key || !value) return;
          setBusy(true);
          try {
            await api.post(basePath, { key, value, env });
            setKey("");
            setValue("");
            load();
          } catch (err) {
            toast.error(errMsg(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Input className="w-44 font-mono" placeholder="KEY" value={key} onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
        <Input className="min-w-40 flex-1 font-mono" type="password" placeholder="value" value={value} onChange={(e) => setValue(e.target.value)} />
        <Select value={env} onValueChange={(v) => setEnv(v as "dev" | "prod")} options={[{ value: "dev", label: "dev" }, { value: "prod", label: "prod" }]} />
        <Button type="submit" loading={busy}>
          <Plus className="h-3.5 w-3.5" /> Add
        </Button>
      </form>
      <div className="mt-3 flex flex-col divide-y divide-line rounded-l border border-line">
        {rows.length === 0 && <div className="p-4 text-center text-[12px] text-muted">No secrets</div>}
        {rows.map((r) => (
          <div key={r.id} className="flex items-center gap-3 px-3 py-2 text-[13px]">
            <KeyRound className="h-3.5 w-3.5 text-muted" />
            <span className="w-48 truncate font-mono">{r.key}</span>
            <span className="flex-1 font-mono text-muted">{r.value}</span>
            <span className="rounded-full bg-hover px-2 text-[11px]">{r.env}</span>
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={async () => {
                const del = basePath.startsWith("/chats") ? `${basePath}/${r.id}` : `${basePath}/${r.id}`;
                await api.del(del).catch((e) => toast.error(errMsg(e)));
                load();
              }}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
