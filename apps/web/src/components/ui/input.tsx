import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-m border border-line-strong bg-card px-3 text-sm text-fg outline-none placeholder:text-subtle focus:border-brand focus:ring-2 focus:ring-brand-soft",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded-m border border-line-strong bg-card px-3 py-2 text-sm text-fg outline-none placeholder:text-subtle focus:border-brand focus:ring-2 focus:ring-brand-soft",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export function Label({ className, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("mb-1.5 block text-[13px] font-medium text-fg-2", className)} {...p} />;
}
