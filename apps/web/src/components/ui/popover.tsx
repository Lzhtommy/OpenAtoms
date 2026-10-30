"use client";
import * as P from "@radix-ui/react-popover";
import { cn } from "@/lib/utils";

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;
export const PopoverAnchor = P.Anchor;
export function PopoverContent({ className, align = "start", ...p }: P.PopoverContentProps) {
  return (
    <P.Portal>
      <P.Content
        align={align}
        sideOffset={6}
        className={cn("z-50 rounded-l border border-line bg-card p-2 text-fg shadow-pop outline-none", className)}
        {...p}
      />
    </P.Portal>
  );
}
