"use client";
import { CheckCircle2, Megaphone, TrendingUp, XCircle } from "lucide-react";
import { safeJson } from "@/lib/utils";
import { PanelBar, PanelEmpty, useBlockSelection } from "./common";

interface SeoDoc {
  url?: string;
  score: number;
  title?: string;
  description?: string;
  checks: { id: string; label: string; ok: boolean; detail: string; weight: number }[];
  history?: number[];
}
interface AdsDoc {
  product?: string;
  objective: string;
  audiences?: string[];
  channels?: string[];
  ad_groups: { name: string; headlines: string[]; descriptions: string[]; keywords?: string[] }[];
  budget?: { daily?: string; split?: Record<string, string> };
  kpis?: string[];
  warnings?: string[];
  path?: string;
}

function ScoreRing({ score }: { score: number }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  const color = score >= 90 ? "var(--color-positive, #41a35d)" : score >= 60 ? "#d87a16" : "#c9444a";
  return (
    <svg width="88" height="88" viewBox="0 0 88 88" aria-label={`SEO score ${score}`}>
      <circle cx="44" cy="44" r={r} fill="none" stroke="currentColor" strokeOpacity={0.08} strokeWidth="8" />
      <circle
        cx="44" cy="44" r={r} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
        strokeDasharray={`${(score / 100) * c} ${c}`} transform="rotate(-90 44 44)"
      />
      <text x="44" y="50" textAnchor="middle" fontSize="20" fontWeight="600" fill="currentColor">{score}</text>
    </svg>
  );
}

function Seo({ d }: { d: SeoDoc }) {
  return (
    <div className="p-4">
      <div className="flex items-center gap-4 rounded-xl border border-line bg-card p-4">
        <ScoreRing score={d.score ?? 0} />
        <div className="min-w-0">
          <div className="text-[15px] font-semibold">SEO score</div>
          <div className="truncate text-[12px] text-muted">{d.title || "(no title)"}</div>
          {d.history && d.history.length > 1 && (
            <div className="mt-1 text-[12px] text-muted">History: {d.history.join(" → ")}</div>
          )}
        </div>
      </div>
      <ul className="mt-3 flex flex-col divide-y divide-line rounded-xl border border-line bg-card">
        {(d.checks ?? []).map((c) => (
          <li key={c.id} className="flex items-start gap-2 p-3 text-[13px]">
            {c.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-positive" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />}
            <div className="min-w-0 flex-1">
              <div>{c.label}</div>
              <div className="truncate text-[11px] text-muted">{c.detail}</div>
            </div>
            <span className="text-[11px] text-subtle">{c.weight}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Ads({ d }: { d: AdsDoc }) {
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="rounded-xl border border-line bg-card p-4">
        <div className="text-[15px] font-semibold">{d.product || "Campaign"}</div>
        <div className="mt-1 text-[13px] text-fg-2">{d.objective}</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(d.channels ?? []).map((c) => (
            <span key={c} className="rounded-full bg-brand-soft px-2 py-0.5 text-[11px] text-brand">{c}</span>
          ))}
          {(d.audiences ?? []).map((a) => (
            <span key={a} className="rounded-full bg-hover px-2 py-0.5 text-[11px]">{a}</span>
          ))}
        </div>
      </div>
      {(d.ad_groups ?? []).map((g) => (
        <div key={g.name} className="rounded-xl border border-line bg-card p-4">
          <div className="mb-2 text-[13px] font-semibold">{g.name}</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {(g.headlines ?? []).map((h, i) => (
              <div key={i} className="rounded-l border border-line p-2.5">
                <div className="text-[13px] font-medium text-brand">{h}</div>
                <div className="text-[12px] text-fg-2">{g.descriptions?.[i % Math.max(1, g.descriptions.length)]}</div>
              </div>
            ))}
          </div>
          {g.keywords && g.keywords.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {g.keywords.map((k) => (
                <code key={k} className="rounded-xs bg-bg-2 px-1.5 py-0.5 text-[11px]">{k}</code>
              ))}
            </div>
          )}
        </div>
      ))}
      {(d.budget?.daily || d.kpis?.length) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {d.budget?.daily && (
            <div className="rounded-xl border border-line bg-card p-4 text-[13px]">
              <div className="mb-1 font-semibold">Budget · {d.budget.daily}/day</div>
              {Object.entries(d.budget.split ?? {}).map(([k, v]) => (
                <div key={k} className="flex justify-between text-fg-2"><span>{k}</span><span>{v}</span></div>
              ))}
            </div>
          )}
          {d.kpis && d.kpis.length > 0 && (
            <div className="rounded-xl border border-line bg-card p-4 text-[13px]">
              <div className="mb-1 font-semibold">KPIs</div>
              {d.kpis.map((k) => <div key={k} className="text-fg-2">• {k}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Growth block: Sarah's SEO audits and Adrian's ad campaigns. */
export function GrowthPanel() {
  const { list, selected } = useBlockSelection(["Growth"]);
  if (!list.length || !selected) return <PanelEmpty icon={TrendingUp} text="No SEO audits or campaigns yet" />;
  const raw = safeJson<Record<string, unknown>>(selected.content || "{}", {});
  const isSeo = Array.isArray((raw as unknown as SeoDoc).checks);
  // a block that has just started (or is still streaming) has no complete document yet
  if (!isSeo && !Array.isArray((raw as unknown as AdsDoc).ad_groups)) {
    return <PanelEmpty icon={selected.command_name?.startsWith("seo") ? TrendingUp : Megaphone} text="Generating…" />;
  }
  return (
    <div className="flex h-full flex-col">
      <PanelBar>
        {isSeo ? <TrendingUp className="h-3.5 w-3.5 text-muted" /> : <Megaphone className="h-3.5 w-3.5 text-muted" />}
        <span className="font-medium">{isSeo ? "SEO audit" : "Ad campaign"}</span>
        {!isSeo && (raw as unknown as AdsDoc).path && <span className="font-mono text-muted">{(raw as unknown as AdsDoc).path}</span>}
      </PanelBar>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {isSeo ? <Seo d={raw as unknown as SeoDoc} /> : <Ads d={raw as unknown as AdsDoc} />}
      </div>
    </div>
  );
}
