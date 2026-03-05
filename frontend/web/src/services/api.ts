/// <reference types="vite/client" />
import { FlashHistoryEntry, HexFile, VcuState } from "../types";

const BASE = "/api";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { error?: string }).error ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const fetchFlashHistory = (): Promise<FlashHistoryEntry[]> =>
  apiFetch("/flash-history");

export const fetchVcuState = (): Promise<{ state: VcuState; powerCycle?: boolean }> =>
  apiFetch("/vcu-state");

export const bootloadOnly = () =>
  apiFetch("/bootload", { method: "POST" });

export const bootAndFlash = (formData: FormData) =>
  apiFetch("/boot-and-flash", { method: "POST", body: formData });

export const flashOnly = (formData: FormData) =>
  apiFetch("/flash-only", { method: "POST", body: formData });

export const fetchFlashLogs = (entryId: string): Promise<{ logs: string[]; status: string }> =>
  apiFetch(`/flash-history/${entryId}/logs`);

export const updateFlashHistoryNotes = (entryId: string, notes: string) =>
  apiFetch(`/flash-history/${entryId}/notes`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });

export const getStoredHexFile = async (fileId: string): Promise<File> => {
  const res = await fetch(`${BASE}/hex-files/${fileId}/content`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const name = disposition.match(/filename="?([^"]+)"?/)?.[1] ?? `${fileId}.hex`;
  return new File([blob], name);
};

export const fetchHexFiles = (): Promise<HexFile[]> =>
  apiFetch("/hex-files");

export const updateHexFileNotes = (fileId: string, notes: string): Promise<HexFile> =>
  apiFetch(`/hex-files/${fileId}/notes`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });

export const pruneOrphanedRecords = (): Promise<{ removedFiles: number; removedHistory: number }> =>
  apiFetch("/prune", { method: "POST" });

export const clearAllData = (): Promise<void> =>
  apiFetch("/clear-all", { method: "DELETE" });
