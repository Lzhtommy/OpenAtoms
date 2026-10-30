"use client";
import * as S from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

export function Switch({ className, ...p }: S.SwitchProps) {
  return (
    <S.Root
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full bg-active transition-colors data-[state=checked]:bg-brand",
        className,
      )}
      {...p}
    >
      <S.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
    </S.Root>
  );
}
