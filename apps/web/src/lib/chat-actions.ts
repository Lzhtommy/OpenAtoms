import { toast } from "sonner";
import { api, errMsg } from "./api";
import type { Boost, Delta, Message, QueueItem, SendMessageBody, Version } from "./types";
import { useChat } from "@/store/chat";
import { loadMessages, loadQueue, loadVersions } from "@/hooks/use-chat-session";

async function wrap<T>(p: Promise<T>): Promise<T | undefined> {
  try {
    return await p;
  } catch (e) {
    toast.error(errMsg(e));
    return undefined;
  }
}

export const chatActions = {
  send: async (chatId: string, body: SendMessageBody) => {
    const r = await wrap(
      api.post<{ message: Message; queued: boolean; queue_item?: QueueItem; boost?: Boost }>(`/chats/${chatId}/messages`, body),
    );
    if (r) {
      if (r.queued) {
        toast.message("Queued — will run after the current task");
        loadQueue(chatId);
      } else if (r.message) useChat.getState().upsertMessage(r.message);
      if (r.boost) useChat.getState().set({ boost: r.boost, isRunning: true, panel: "boost", follow: false });
    }
    return r;
  },
  sendText: (chatId: string, text: string, metadata?: SendMessageBody["metadata"]) =>
    chatActions.send(chatId, { content: [{ insert: text }], type: "message", metadata }),
  stop: (chatId: string) => wrap(api.post(`/chats/${chatId}/stop`)),
  edit: async (chatId: string, mid: number, content: Delta) => {
    const r = await wrap(api.post(`/chats/${chatId}/messages/${mid}/edit`, { content }));
    if (r !== undefined) await loadMessages(chatId);
  },
  retry: async (chatId: string, mid: number) => {
    const r = await wrap(api.post(`/chats/${chatId}/messages/${mid}/retry`));
    if (r !== undefined) await loadMessages(chatId);
  },
  switchAttempt: async (chatId: string, roundId: number, attempt: number) => {
    const r = await wrap(api.put(`/chats/${chatId}/round-state`, { active_attempts: { [roundId]: attempt } }));
    if (r !== undefined) await loadMessages(chatId);
  },
  approvePlan: (chatId: string) => wrap(api.post(`/chats/${chatId}/approve-plan`)),
  declinePlan: (chatId: string, reason?: string) => wrap(api.post(`/chats/${chatId}/decline-plan`, { reason })),
  submitQuestions: (chatId: string, messageId: number, answers: Record<string, string | string[]>) =>
    wrap(api.post(`/chats/${chatId}/submit-questions`, { message_id: messageId, answers })),
  queue: {
    add: (chatId: string, content: Delta) => wrap(api.post(`/chats/${chatId}/prompt-queue`, { content })),
    update: (chatId: string, id: number, content: Delta) => wrap(api.patch(`/chats/${chatId}/prompt-queue/${id}`, { content })),
    remove: (chatId: string, id: number) => wrap(api.del(`/chats/${chatId}/prompt-queue/${id}`)),
    reorder: (chatId: string, ids: number[]) => wrap(api.post(`/chats/${chatId}/prompt-queue/reorder`, { ids })),
    pause: (chatId: string, paused: boolean) => wrap(api.post(`/chats/${chatId}/prompt-queue/pause`, { paused })),
    sendNow: (chatId: string, id: number) => wrap(api.post(`/chats/${chatId}/prompt-queue/${id}/send`)),
  },
  restore: async (chatId: string, version: string) => {
    const r = await wrap(api.post<Version>(`/chats/${chatId}/versions/${version}/restore`));
    if (r) {
      toast.success(`Restored to ${version}`);
      loadVersions(chatId);
      loadMessages(chatId);
    }
    return r;
  },
};

export const boostActions = {
  select: async (chatId: string, index: number) => {
    const r = await wrap(api.post<Boost & { version: string | null }>(`/chats/${chatId}/boost/select`, { index }));
    if (r) {
      useChat.getState().set({ boost: r });
      toast.success(`Merged candidate #${index + 1}${r.version ? ` → ${r.version}` : ""}`);
      await loadVersions(chatId);
    }
    return r;
  },
  abort: (chatId: string) => wrap(api.post<Boost | null>(`/chats/${chatId}/boost_abort`)),
  discard: async (chatId: string) => {
    const r = await wrap(api.del<Boost | null>(`/chats/${chatId}/boost`));
    if (r !== undefined) useChat.getState().set({ boost: null, panel: "appviewer" });
  },
};
