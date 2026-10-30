"use client";
import * as S from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function Select({
  value,
  onValueChange,
  options,
  className,
  placeholder,
}: {
  value: string;
  onValueChange: (v: string) => void;
  options: { value: string; label: React.ReactNode; desc?: React.ReactNode }[];
  className?: string;
  placeholder?: string;
}) {
  return (
    <S.Root value={value} onValueChange={onValueChange}>
      <S.Trigger
        className={cn(
          "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border border-line-strong bg-card px-3 text-[13px] text-fg-2 outline-none hover:bg-hover",
          className,
        )}
      >
        <S.Value placeholder={placeholder} />
        <S.Icon>
          <ChevronDown className="h-3.5 w-3.5 opacity-60" />
        </S.Icon>
      </S.Trigger>
      <S.Portal>
        <S.Content position="popper" sideOffset={6} className="z-50 max-h-[320px] min-w-[180px] overflow-hidden rounded-l border border-line bg-card p-1 shadow-pop">
          <S.Viewport>
            {options.map((o) => (
              <S.Item
                key={o.value}
                value={o.value}
                className="relative flex cursor-pointer flex-col rounded-m py-1.5 pr-7 pl-2.5 text-[13px] outline-none data-[highlighted]:bg-hover"
              >
                <S.ItemText>{o.label}</S.ItemText>
                {o.desc && <span className="text-[11px] text-muted">{o.desc}</span>}
                <S.ItemIndicator className="absolute top-2 right-2">
                  <Check className="h-3.5 w-3.5 text-brand" />
                </S.ItemIndicator>
              </S.Item>
            ))}
          </S.Viewport>
        </S.Content>
      </S.Portal>
    </S.Root>
  );
}
