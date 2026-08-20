// 协同看板 · API 客户端（token 存 localStorage）
import type { BoardPayload, Me, RoomCard, Task } from "./types";

const TOKEN_KEY = "board_token";
const USER_KEY = "board_user";

export function loadToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function loadUser(): Me | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as Me) : null;
  } catch {
    return null;
  }
}

function saveSession(token: string, user: Me) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = loadToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function apiRegister(username: string, password: string): Promise<{ token: string; user: Me }> {
  const out = await request<{ token: string; user: Me }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  saveSession(out.token, out.user);
  return out;
}

export async function apiLogin(username: string, password: string): Promise<{ token: string; user: Me }> {
  const out = await request<{ token: string; user: Me }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  saveSession(out.token, out.user);
  return out;
}

export async function apiMe(): Promise<Me> {
  return request("/api/auth/me");
}

export async function apiPatchMe(patch: { display_name?: string; color?: string }): Promise<Me> {
  const me = await request<Me>("/api/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
  localStorage.setItem(USER_KEY, JSON.stringify(me));
  return me;
}

export async function apiRooms(): Promise<RoomCard[]> {
  const body = await request<{ rooms: RoomCard[] }>("/api/rooms");
  return body.rooms;
}

export async function apiCreateRoom(name: string): Promise<{ room: { id: number; name: string; invite_code: string } }> {
  return request("/api/rooms", { method: "POST", body: JSON.stringify({ name }) });
}

export async function apiJoinRoom(roomId: number, code: string) {
  return request<{ room: { id: number; name: string }; role: string; already: boolean }>(
    `/api/rooms/${roomId}/join`,
    { method: "POST", body: JSON.stringify({ invite_code: code }) },
  );
}

export async function apiJoinByCode(code: string) {
  return request<{ room: { id: number; name: string }; role: string; already: boolean }>(
    "/api/rooms/join-by-code",
    { method: "POST", body: JSON.stringify({ invite_code: code }) },
  );
}

export async function apiMembers(roomId: number) {
  const body = await request<{ members: import("./types").Member[] }>(`/api/rooms/${roomId}/members`);
  return body.members;
}

export async function apiRefreshInvite(roomId: number): Promise<{ invite_code: string }> {
  return request(`/api/rooms/${roomId}/invite/refresh`, { method: "POST" });
}

export async function apiRemoveMember(roomId: number, userId: number) {
  return request(`/api/rooms/${roomId}/members/${userId}`, { method: "DELETE" });
}

export async function apiBoard(roomId: number, version: number): Promise<BoardPayload> {
  return request(`/api/rooms/${roomId}/board?version=${version}`);
}

export async function apiCreateTask(roomId: number, body: Record<string, unknown>): Promise<{ task: Task }> {
  return request(`/api/rooms/${roomId}/tasks`, { method: "POST", body: JSON.stringify(body) });
}

export async function apiPatchTask(roomId: number, taskId: number, body: Record<string, unknown>): Promise<{ task: Task }> {
  return request(`/api/rooms/${roomId}/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function apiDeleteTask(roomId: number, taskId: number) {
  return request(`/api/rooms/${roomId}/tasks/${taskId}`, { method: "DELETE" });
}

export async function apiStats(roomId: number) {
  return request<{
    stats: import("./types").BoardStats;
    by_member: { user_id: number; username: string; display_name: string; color: string;
                 created: number; completed: number; overdue: number }[];
  }>(`/api/rooms/${roomId}/stats`);
}
