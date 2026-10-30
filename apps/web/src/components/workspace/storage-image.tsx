"use client";
import { useEffect, useState } from "react";
import { api, request } from "@/lib/api";
import { useChat } from "@/store/chat";

/** Image from a workspace/.storage path (authed fetch) or a data:/http URL. */
export function StorageImage({ src, className, alt = "" }: { src: string; className?: string; alt?: string }) {
  const chatId = useChat((s) => s.chatId);
  const [url, setUrl] = useState<string | null>(/^(data:|https?:)/.test(src) ? src : null);
  useEffect(() => {
    if (/^(data:|https?:)/.test(src)) {
      setUrl(src);
      return;
    }
    let u: string | null = null;
    const req = src.startsWith("/api/")
      ? request<Response>(src, { raw: true })
      : api.get<Response>(`/chats/${chatId}/files/raw`, { path: src }, { raw: true });
    req
      .then((r) => r.blob())
      .then((b) => setUrl((u = URL.createObjectURL(b))))
      .catch(() => setUrl(null));
    return () => {
      if (u) URL.revokeObjectURL(u);
    };
  }, [src, chatId]);
  if (!url) return <div className={`skeleton ${className ?? ""}`} />;
  return <img src={url} alt={alt} className={className} />;
}
