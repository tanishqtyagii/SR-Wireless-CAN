import { FlashHistoryEntry } from "../types";

const FLASH_LOGS_CACHE_KEY = "sr_flash_history_logs_v1";
const MAX_CACHED_ENTRIES = 250;

type PersistedFlashLogsCache = Array<[string, string[]]>;

function loadPersistedCache(): PersistedFlashLogsCache {
  try {
    return JSON.parse(localStorage.getItem(FLASH_LOGS_CACHE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function savePersistedCache(cache: Map<string, string[]>): void {
  try {
    const serialized = JSON.stringify(Array.from(cache.entries()).slice(-MAX_CACHED_ENTRIES));
    localStorage.setItem(FLASH_LOGS_CACHE_KEY, serialized);
  } catch {
    // Ignore quota failures.
  }
}

const _cachedLogsByHistory = new Map<string, string[]>(loadPersistedCache());

export function getCachedFlashLogs(entryId: string): string[] | undefined {
  return _cachedLogsByHistory.get(entryId);
}

export function setCachedFlashLogs(entryId: string, logs: string[]): void {
  _cachedLogsByHistory.set(entryId, logs);
  savePersistedCache(_cachedLogsByHistory);
}

export function hydrateFlashHistoryLogs(entries: FlashHistoryEntry[]): FlashHistoryEntry[] {
  return entries.map((entry) => {
    if (entry.logs !== undefined) {
      _cachedLogsByHistory.set(entry.id, entry.logs);
      return entry;
    }

    const cachedLogs = _cachedLogsByHistory.get(entry.id);
    return cachedLogs ? { ...entry, logs: cachedLogs } : entry;
  });
}
