"use client";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({
  className,
  children,
  title,
  description,
  side,
  ...p
}: D.DialogContentProps & { title?: React.ReactNode; description?: React.ReactNode; side?: "right" }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[1px]" />
      <D.Content
        className={cn(
          "fixed z-50 border border-line bg-card text-fg shadow-pop outline-none",
          side === "right"
            ? "top-0 right-0 h-full w-[420px] max-w-[100vw] overflow-y-auto"
            : "top-1/2 left-1/2 max-h-[85vh] w-[480px] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl",
          className,
        )}
        {...p}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-4">
          <div>
            <D.Title className="text-[15px] font-semibold">{title}</D.Title>
            {description ? (
              <D.Description className="mt-1 text-[13px] text-muted">{description}</D.Description>
            ) : (
              <D.Description className="sr-only">dialog</D.Description>
            )}
          </div>
          <D.Close className="rounded-full p-1 text-muted hover:bg-hover hover:text-fg">
            <X className="h-4 w-4" />
          </D.Close>
        </div>
        <div className="px-5 pt-3 pb-5">{children}</div>
      </D.Content>
    </D.Portal>
  );
}
