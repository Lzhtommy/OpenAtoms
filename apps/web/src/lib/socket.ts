"use client";
import { io, type Socket } from "socket.io-client";
import { tokenStore } from "./api";

let socket: Socket | null = null;
let socketToken: string | null = null;

function socketUrl() {
  return process.env.NEXT_PUBLIC_OA_SOCKET_URL || "http://127.0.0.1:8100";
}

export function getSocket(): Socket {
  const token = tokenStore.get();
  if (socket && socketToken === token) return socket;
  if (socket) socket.disconnect();
  socketToken = token;
  socket = io(socketUrl(), {
    path: "/socket.io",
    transports: ["websocket"],
    auth: (cb) => cb({ token: tokenStore.get() }),
    query: token ? { token } : undefined,
    timeout: 5000,
    reconnection: true,
    reconnectionAttempts: 10,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
  });
  return socket;
}

export function shareSocket(): Socket {
  // anonymous socket is not supported by contract; share pages are static replays
  return getSocket();
}
