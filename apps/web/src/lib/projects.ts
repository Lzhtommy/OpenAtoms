import type { PromptSubmit } from "@/components/home/prompt-box";
import { api } from "./api";
import type { Chat, DeltaOp } from "./types";

export async function createProject(p: PromptSubmit): Promise<Chat> {
  const metadata = {
    agent_mode: p.agent_mode,
    plan_mode: p.plan_mode,
    default_model: p.model || undefined,
    enable_dr: p.enable_dr || undefined,
  };
  const base = {
    title: p.text.slice(0, 30),
    llm: p.model ? { default_model: p.model } : undefined,
    config: { agent_mode: p.agent_mode, plan_mode: p.plan_mode, template_name: p.template_name ?? "react-shadcn-vite" },
  };
  const boost = p.boost_models && p.boost_models.length >= 2 ? { use_boost: true, boost_models: p.boost_models } : {};
  if (!p.files.length && !p.boost_models) {
    return api.post<Chat>("/chats", { ...base, first_message: { content: [{ insert: p.text }], type: "message", metadata } });
  }
  const chat = await api.post<Chat>("/chats", base);
  // The first message can still be rejected (boost capacity, moderation, quota): don't leave an empty project behind.
  try {
    const ops: DeltaOp[] = [];
    const attachments: { name: string; path: string; url: string; mime: string }[] = [];
    for (const f of p.files) {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("dir", "uploads");
      const r = await api.upload<{ path: string; url: string }>(`/chats/${chat.chat_id}/files/upload`, fd);
      attachments.push({ name: f.name, path: r.path, url: r.url, mime: f.type });
      ops.push({ insert: { attachment: { name: f.name, url: r.url, mime: f.type } } });
    }
    await api.post(`/chats/${chat.chat_id}/messages`, {
      content: [...ops, { insert: p.text }],
      type: "message",
      metadata: { ...metadata, attachments: attachments.length ? attachments : undefined },
      ...boost,
    });
  } catch (e) {
    await api.del(`/chats/${chat.chat_id}`).catch(() => undefined);
    throw e;
  }
  return chat;
}

