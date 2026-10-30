import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "brand" | "secondary" | "ghost" | "outline" | "danger" | "link";
type Size = "xs" | "sm" | "md" | "lg" | "icon" | "icon-sm";

const variants: Record<Variant, string> = {
  primary: "bg-inverse text-inverse-fg hover:opacity-90",
  brand: "bg-brand text-white hover:bg-brand-hover",
  secondary: "bg-hover text-fg hover:bg-active",
  ghost: "text-fg-2 hover:bg-hover hover:text-fg",
  outline: "border border-line-strong bg-card text-fg hover:bg-hover",
  danger: "bg-danger text-white hover:opacity-90",
  link: "text-brand hover:underline px-0",
};
const sizes: Record<Size, string> = {
  xs: "h-6 px-2 text-xs gap-1",
  sm: "h-7 px-3 text-[13px] gap-1.5",
  md: "h-8 px-3.5 text-sm gap-1.5",
  lg: "h-10 px-5 text-[15px] gap-2",
  icon: "h-8 w-8",
  "icon-sm": "h-7 w-7",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-full font-medium whitespace-nowrap transition-colors select-none disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
