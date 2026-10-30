"use client";
import * as M from "@radix-ui/react-dropdown-menu";
import * as React from "react";
import { cn } from "@/lib/utils";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;
export const MenuSub = M.Sub;

export function MenuContent({ className, align = "start", ...p }: M.DropdownMenuContentProps) {
  return (
    <M.Portal>
      <M.Content
        align={align}
        sideOffset={6}
        className={cn("z-50 min-w-[180px] rounded-l border border-line bg-card p-1 text-fg shadow-pop", className)}
        {...p}
      />
    </M.Portal>
  );
}
export function MenuItem({ className, danger, ...p }: M.DropdownMenuItemProps & { danger?: boolean }) {
  return (
    <M.Item
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-m px-2.5 py-1.5 text-[13px] outline-none select-none data-[highlighted]:bg-hover",
        danger && "text-danger",
        className,
      )}
      {...p}
    />
  );
}
export function MenuSep() {
  return <M.Separator className="my-1 h-px bg-line" />;
}
export function MenuLabel({ children }: { children: React.ReactNode }) {
  return <M.Label className="px-2.5 py-1 text-[11px] font-medium text-muted">{children}</M.Label>;
}
export function MenuSubTrigger({ className, ...p }: M.DropdownMenuSubTriggerProps) {
  return (
    <M.SubTrigger
      className={cn("flex cursor-pointer items-center gap-2 rounded-m px-2.5 py-1.5 text-[13px] outline-none data-[highlighted]:bg-hover data-[state=open]:bg-hover", className)}
      {...p}
    />
  );
}
export function MenuSubContent({ className, ...p }: M.DropdownMenuSubContentProps) {
  return (
    <M.Portal>
      <M.SubContent className={cn("z-50 min-w-[160px] rounded-l border border-line bg-card p-1 shadow-pop", className)} {...p} />
    </M.Portal>
  );
}
