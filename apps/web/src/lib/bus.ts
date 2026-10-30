// Tiny frontend-internal event bus (chat:show-block, appviewer:*, editor:*).
type Fn = (payload: any) => void;
const listeners = new Map<string, Set<Fn>>();

export const bus = {
  on(evt: string, fn: Fn) {
    if (!listeners.has(evt)) listeners.set(evt, new Set());
    listeners.get(evt)!.add(fn);
    return () => listeners.get(evt)?.delete(fn);
  },
  emit(evt: string, payload?: any) {
    listeners.get(evt)?.forEach((fn) => fn(payload));
  },
};
