"use client";
import { Eye } from "lucide-react";
import Link from "next/link";
import { UserAvatar } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";

/** Soft tinted backdrops for projects that have no screenshot yet; picked by title hash so a project keeps its tone. */
const TONES = [
  ["#e9eeff", "#c9d5ff"],
  ["#eef4ee", "#cfe3d3"],
  ["#f6efe9", "#ead6c6"],
  ["#f0ecf7", "#d9cff0"],
  ["#eaf3f6", "#c7e0e8"],
  ["#f4f1e6", "#e4dcbc"],
];
const DARK_TONES = [
  ["#1d2340", "#2a3570"],
  ["#1b2a20", "#28452f"],
  ["#2d221b", "#4a3526"],
  ["#251f33", "#3b2f57"],
  ["#1a2a30", "#284650"],
  ["#2a2719", "#463f24"],
];

function hash(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function Cover({
  src,
  title,
  className,
}: {
  src?: string | null;
  title: string;
  className?: string;
}) {
  if (src) {
    return (
      <div
        className={cn(
          "relative aspect-[16/9] overflow-hidden bg-bg-2",
          className,
        )}
      >
        <img
          src={src}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover object-top transition-transform duration-500 ease-out group-hover:scale-[1.03]"
        />
      </div>
    );
  }
  const i = hash(title) % TONES.length;
  const [a, b] = TONES[i];
  const [da, db] = DARK_TONES[i];
  return (
    <div
      className={cn(
        "relative flex aspect-[16/9] items-end overflow-hidden p-4",
        className,
      )}
      style={{
        ["--c1" as string]: a,
        ["--c2" as string]: b,
        ["--dc1" as string]: da,
        ["--dc2" as string]: db,
      }}
    >
      <div className="absolute inset-0 bg-[radial-gradient(120%_90%_at_100%_0%,var(--c2),var(--c1)_70%)] dark:bg-[radial-gradient(120%_90%_at_100%_0%,var(--dc2),var(--dc1)_70%)]" />
      <span className="font-display relative line-clamp-2 text-[22px] leading-tight text-fg/70 transition-transform duration-500 ease-out group-hover:-translate-y-0.5">
        {title}
      </span>
    </div>
  );
}

export function ProjectCard({
  href,
  title,
  cover,
  author,
  meta,
  views,
  badge,
  actions,
  external,
}: {
  href: string;
  title: string;
  cover?: string | null;
  author?: { name: string; avatar?: string | null } | null;
  meta?: React.ReactNode;
  views?: number;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  external?: boolean;
}) {
  return (
    <div className="group relative overflow-hidden rounded-xl border border-line bg-card transition-[border-color,box-shadow,transform] duration-300 hover:-translate-y-0.5 hover:border-line-strong hover:shadow-pop">
      <Link
        href={href}
        target={external ? "_blank" : undefined}
        className="block"
      >
        <Cover src={cover} title={title} />
        {badge && <div className="absolute top-3 left-3">{badge}</div>}
      </Link>
      <div className="flex items-center gap-3 px-4 py-3">
        {author && (
          <UserAvatar
            name={author.name}
            src={author.avatar}
            size={32}
            className="shrink-0"
          />
        )}
        <div className="min-w-0 flex-1">
          <Link
            href={href}
            target={external ? "_blank" : undefined}
            className="block truncate text-[14px] font-medium"
          >
            {title}
          </Link>
          <div className="mt-0.5 flex items-center gap-1.5 truncate text-[12px] text-muted">
            {author && <span className="truncate">{author.name}</span>}
            {meta}
            {views !== undefined && (
              <span className="flex shrink-0 items-center gap-1">
                {author && <span className="text-subtle">·</span>}
                <Eye className="h-3.5 w-3.5" />
                {views}
              </span>
            )}
          </div>
        </div>
        {actions}
      </div>
    </div>
  );
}

export function ProjectCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card">
      <div className="skeleton aspect-[16/9]" />
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="skeleton h-8 w-8 rounded-full" />
        <div className="flex-1 space-y-2">
          <div className="skeleton h-3 w-2/3 rounded" />
          <div className="skeleton h-2.5 w-1/3 rounded" />
        </div>
      </div>
    </div>
  );
}
