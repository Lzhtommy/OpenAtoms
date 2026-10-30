"use client";
import * as T from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = T.Root;
export const TabsContent = T.Content;
export function TabsList({ className, ...p }: T.TabsListProps) {
  return <T.List className={cn("inline-flex items-center gap-1 rounded-full bg-hover p-1", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: T.TabsTriggerProps) {
  return (
    <T.Trigger
      className={cn(
        "cursor-pointer rounded-full px-3 py-1 text-[13px] text-muted transition-colors data-[state=active]:bg-card data-[state=active]:text-fg data-[state=active]:shadow-card",
        className,
      )}
      {...p}
    />
  );
}
