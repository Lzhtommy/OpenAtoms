import { cn } from "@/lib/utils";
import { agentFor } from "@/lib/agents";

export function UserAvatar({ name, src, size = 28, className }: { name?: string | null; src?: string | null; size?: number; className?: string }) {
  const letter = (name || "?").trim().charAt(0).toUpperCase();
  if (src) return <img src={src} alt={name ?? ""} width={size} height={size} className={cn("rounded-full object-cover", className)} style={{ width: size, height: size }} />;
  return (
    <span
      className={cn("inline-flex items-center justify-center rounded-full bg-brand font-medium text-white", className)}
      style={{ width: size, height: size, fontSize: size * 0.42 }}
    >
      {letter}
    </span>
  );
}

export function AgentAvatar({ role, name, size = 28, className, ring }: { role: string; name?: string; size?: number; className?: string; ring?: boolean }) {
  const a = agentFor(role, name);
  if (!a) return <UserAvatar name={name || role} size={size} className={className} />;
  return (
    <img
      src={a.avatar}
      alt={a.name}
      title={`${a.name} · ${a.role}`}
      className={cn("rounded-full bg-bg-2 object-cover", ring && "ring-2 ring-card", className)}
      style={{ width: size, height: size }}
    />
  );
}
