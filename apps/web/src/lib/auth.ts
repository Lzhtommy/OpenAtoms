"use client";
import { create } from "zustand";
import { api, tokenStore } from "./api";
import type { User, Workspace } from "./types";

interface Balance {
  credits: number;
  plan: string;
  monthly_quota: number;
  used_this_month: number;
}

interface AuthState {
  user: User | null;
  workspaces: Workspace[];
  workspaceId: number | null;
  balance: Balance | null;
  loaded: boolean;
  load: () => Promise<User | null>;
  setToken: (t: string) => void;
  logout: () => Promise<void>;
  switchWorkspace: (id: number) => void;
  refreshBalance: () => Promise<void>;
  refreshWorkspaces: () => Promise<void>;
  setUser: (u: User) => void;
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  workspaces: [],
  workspaceId: null,
  balance: null,
  loaded: false,
  setToken: (t) => tokenStore.set(t),
  setUser: (u) => set({ user: u }),
  load: async () => {
    if (!tokenStore.get()) {
      set({ loaded: true, user: null });
      return null;
    }
    try {
      const user = await api.get<User>("/user/detail", undefined, { noAuthRedirect: true });
      let workspaces: Workspace[] = [];
      try {
        workspaces = await api.get<Workspace[]>("/workspaces");
      } catch {
        /* ignore */
      }
      const stored = Number(tokenStore.workspace() || 0);
      const wid =
        workspaces.find((w) => w.id === stored)?.id ?? user.current_workspace_id ?? workspaces[0]?.id ?? null;
      tokenStore.setWorkspace(wid);
      set({ user, workspaces, workspaceId: wid, loaded: true });
      get().refreshBalance();
      return user;
    } catch {
      set({ loaded: true, user: null });
      return null;
    }
  },
  refreshWorkspaces: async () => {
    const workspaces = await api.get<Workspace[]>("/workspaces");
    set({ workspaces });
  },
  refreshBalance: async () => {
    try {
      const balance = await api.get<Balance>("/transaction/balance");
      set({ balance });
    } catch {
      /* ignore */
    }
  },
  logout: async () => {
    try {
      await api.post("/user/logout");
    } catch {
      /* ignore */
    }
    tokenStore.set(null);
    tokenStore.setWorkspace(null);
    set({ user: null, workspaces: [], workspaceId: null, balance: null });
  },
  switchWorkspace: (id) => {
    tokenStore.setWorkspace(id);
    set({ workspaceId: id });
    get().refreshBalance();
  },
}));
