"use client";
import { create } from "zustand";
import type { DesignSelection, DesignSelectionRef, StagedEdit } from "@/lib/types";

/** Per-chat visual editing state (design mode, current pick, staged edits, composer attachment). */
interface VisualEditState {
  chatId: string | null;
  active: boolean;
  selection: DesignSelection | null;
  staged: StagedEdit[];
  /** selection attached to the composer by "Ask AI about this" */
  attached: DesignSelectionRef | null;
  reset: (chatId: string) => void;
  set: (p: Partial<VisualEditState>) => void;
}

export const useVisualEdit = create<VisualEditState>((set) => ({
  chatId: null,
  active: false,
  selection: null,
  staged: [],
  attached: null,
  reset: (chatId) => set({ chatId, active: false, selection: null, staged: [], attached: null }),
  set: (p) => set(p),
}));

export function selectionRef(s: DesignSelection): DesignSelectionRef {
  return { loc: s.loc, selector: s.selector, tag: s.tag, text: s.text.slice(0, 200), classes: s.classes };
}
