"use client";
import { Eye, Heart } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { UserAvatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/card";
import { api, errMsg } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Page } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Item {
  chat_id: string;
  title: string;
  cover: string | null;
  link: string | null;
  likes: number;
  views: number;
  is_liked?: boolean;
  author: { name: string; avatar: string | null } | string | null;
}

function Inner() {
  const t = useT();
  const [sort, setSort] = useState<"hot" | "new">("hot");
  const [items, setItems] = useState<Item[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = async (p: number) => {
    setLoading(true);
    try {
      const d = await api.get<Page<Item> | Item[]>("/discover", { sort, cur_page: p, page_num: 24 });
      const list = Array.isArray(d) ? d : d.items;
      setItems(p === 1 ? list : (x) => [...x, ...list]);
      setHasMore(Array.isArray(d) ? false : d.has_more);
      setPage(p);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load(1);
  }, [sort]);

  const like = async (it: Item) => {
    try {
      if (it.is_liked) await api.del(`/chats/${it.chat_id}/like`);
      else await api.post(`/chats/${it.chat_id}/like`);
      setItems((xs) => xs.map((x) => (x.chat_id === it.chat_id ? { ...x, is_liked: !it.is_liked, likes: x.likes + (it.is_liked ? -1 : 1) } : x)));
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <div className="flex items-center gap-3">
        <h1 className="font-serif text-[28px]">{t("discover")}</h1>
        <div className="flex-1" />
        <div className="inline-flex rounded-full bg-hover p-0.5">
          {(["hot", "new"] as const).map((s) => (
            <button key={s} onClick={() => setSort(s)} className={cn("cursor-pointer rounded-full px-3 py-1 text-[12px] capitalize", sort === s && "bg-card shadow-card")}>
              {s}
            </button>
          ))}
        </div>
      </div>
      {!loading && items.length === 0 ? (
        <Empty className="mt-10">No public projects yet</Empty>
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((it) => {
            const author = typeof it.author === "string" ? { name: it.author, avatar: null } : it.author;
            return (
              <div key={it.chat_id} className="overflow-hidden rounded-xl border border-line bg-card shadow-card transition-shadow hover:shadow-pop">
                <Link href={`/share/${it.chat_id}`}>
                  <div className="aspect-[16/9] bg-bg-2">
                    {it.cover ? (
                      <img src={it.cover} alt="" className="h-full w-full object-cover" />
                    ) : (
                      <div className="flex h-full items-center justify-center font-serif text-[40px] text-subtle">{it.title.charAt(0)}</div>
                    )}
                  </div>
                </Link>
                <div className="p-3">
                  <Link href={`/share/${it.chat_id}`} className="block truncate text-[14px] font-medium hover:underline">
                    {it.title}
                  </Link>
                  <div className="mt-2 flex items-center gap-2 text-[12px] text-muted">
                    <UserAvatar name={author?.name} src={author?.avatar} size={18} />
                    <span className="min-w-0 flex-1 truncate">{author?.name}</span>
                    <span className="flex items-center gap-1">
                      <Eye className="h-3.5 w-3.5" />
                      {it.views}
                    </span>
                    <button onClick={() => like(it)} className="flex cursor-pointer items-center gap-1">
                      <Heart className={cn("h-3.5 w-3.5", it.is_liked && "fill-danger text-danger")} />
                      {it.likes}
                    </button>
                    {it.link && (
                      <a href={it.link} target="_blank" rel="noreferrer" className="text-brand hover:underline">
                        {t("open")}
                      </a>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {hasMore && (
        <div className="mt-6 flex justify-center">
          <Button variant="outline" loading={loading} onClick={() => load(page + 1)}>
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}

export default function Discover() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}
