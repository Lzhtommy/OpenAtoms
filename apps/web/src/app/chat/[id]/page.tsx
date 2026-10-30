"use client";
import { useParams } from "next/navigation";
import { FullPageLoader, useRequireAuth } from "@/components/shell/auth-guard";
import { Workbench } from "@/components/chat/workbench";
import { useChatSession } from "@/hooks/use-chat-session";
import { useChat } from "@/store/chat";

function Session({ id }: { id: string }) {
  useChatSession(id);
  const chat = useChat((s) => s.chat);
  if (!chat || chat.chat_id !== id) return <FullPageLoader />;
  return <Workbench />;
}

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const { ready } = useRequireAuth();
  if (!ready) return <FullPageLoader />;
  return <Session id={id} />;
}
