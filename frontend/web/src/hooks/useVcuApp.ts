import { useState, useCallback, useEffect, useRef } from "react";
import { useVcuStore } from "../store/vcuStore";
import {
  bootAndFlash,
  bootloadOnly,
  fetchFlashHistory,
  fetchFlashLogs,
  fetchVcuState,
  flashOnly,
  getStoredHexFile,
  pruneOrphanedRecords,
  updateFlashHistoryNotes,
} from "../services/api";
import { subscribeToBroadcast } from "../services/ws";
import { FlashHistoryEntry } from "../types";

// Module-level cache so history survives tab switches without a visible reload
const HISTORY_STORAGE_KEY = "sr_flash_history";
function loadPersistedHistory(): FlashHistoryEntry[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) ?? "[]"); } catch { return []; }
}
function savePersistedHistory(items: FlashHistoryEntry[]): void {
  try { localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(items)); } catch { /* storage full */ }
}
let _cachedHistory: FlashHistoryEntry[] = loadPersistedHistory();

// Log persistence: load from localStorage on startup, survive hard refreshes
const LOGS_STORAGE_KEY = "sr_flash_logs";
const LOGS_MAX_LINES = 4000;
function loadPersistedLogs(): string[] {
  try { return JSON.parse(localStorage.getItem(LOGS_STORAGE_KEY) ?? "[]"); } catch { return []; }
}
function savePersistedLogs(lines: string[]): void {
  const capped = lines.length > LOGS_MAX_LINES ? lines.slice(lines.length - LOGS_MAX_LINES) : lines;
  _persistedLogs = capped;
  try { localStorage.setItem(LOGS_STORAGE_KEY, JSON.stringify(capped)); } catch { /* storage full */ }
}
let _persistedLogs: string[] = loadPersistedLogs();
let _bootstrapped = false;
// Track which operation IDs are already in the buffer
let _loggedIds = new Set<string>(
  (_persistedLogs.join("\n").match(/Operation (fh_\w+)/g) ?? []).map(s => s.replace("Operation ", ""))
);

/** Fetch logs for ALL history entries and rebuild a fully sorted chronological buffer */
async function bootstrapHistoricalLogs(entries: FlashHistoryEntry[]): Promise<string[]> {
  // Sort oldest-first
  const sorted = [...entries].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  const lines: string[] = [];
  for (const entry of sorted) {
    try {
      const data = await fetchFlashLogs(entry.id);
      const ts = new Date(entry.timestamp).toLocaleTimeString("en-US", {
        timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
      });
      lines.push("", `── Operation ${entry.id}  ${ts} PST ──────────────────────`);
      lines.push(...(data.logs ?? []));
    } catch { /* skip if fetch fails */ }
  }
  return lines;
}

export function useVcuApp() {
  const { setVcuState, vcuState, setPowerCycleNeeded, powerCycleNeeded } = useVcuStore();
  
  const [history, setHistory] = useState<FlashHistoryEntry[]>(_cachedHistory);
  
  const [isBusy, setIsBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [backendDown, setBackendDown] = useState(false);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [liveLogs, setLiveLogs] = useState<string[]>(_persistedLogs);
  const livePollRef = useRef<number | null>(null);
  // Accumulated logs that persist across all operations for the lifetime of the page
  const accumulatedLogsRef = useRef<string[]>(_persistedLogs);
  // How many lines from the current operation we've already appended
  const lastOpLogCountRef = useRef<number>(0);
  // Ref so refreshData can check if a live poll is running without a stale closure
  const activeHistoryIdRef = useRef<string | null>(null);

  const refreshData = useCallback(async () => {
    try {
      const [historyItems, statePayload] = await Promise.all([
        fetchFlashHistory(),
        fetchVcuState(),
      ]);

      setBackendDown(false);
      const items = Array.isArray(historyItems) ? historyItems : [];
      _cachedHistory = items;
      savePersistedHistory(items);
      setHistory(items);
      if (statePayload?.state) {
        setVcuState(statePayload.state);
      }
      setPowerCycleNeeded(statePayload?.powerCycle ?? false);

      // Bootstrap once on page load, or whenever a new operation appears that isn't logged yet
      const hasNew = items.some(e => !_loggedIds.has(e.id));
      if (!_bootstrapped || hasNew) {
        _bootstrapped = true;
        bootstrapHistoricalLogs(items).then((lines) => {
          if (lines.length > 0) {
            _loggedIds = new Set(items.map(e => e.id));
            savePersistedLogs(lines);
            // Update accumulatedLogsRef and React state — but only when not mid-operation
            if (!activeHistoryIdRef.current) {
              accumulatedLogsRef.current = lines;
              setLiveLogs([...lines]);
            }
          }
        }).catch(() => {});
      }
    } catch (err: unknown) {
      // TypeError = raw network failure; HTTP 5xx = Vite proxy couldn't reach backend
      const isNetworkError = err instanceof TypeError;
      const isProxyError = err instanceof Error && /^HTTP (5\d{2})$/.test(err.message);
      if (isNetworkError || isProxyError) {
        setBackendDown(true);
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to fetch data.";
      setErrorMessage(message);
    }
  }, [setVcuState]);

  useEffect(() => {
    pruneOrphanedRecords().finally(() => refreshData());
    const pollHandle = window.setInterval(refreshData, 5000);
    return () => window.clearInterval(pollHandle);
  }, [refreshData]);

  // Fast log polling while an operation is running
  useEffect(() => {
    activeHistoryIdRef.current = activeHistoryId;
    if (livePollRef.current !== null) {
      window.clearInterval(livePollRef.current);
      livePollRef.current = null;
    }
    if (!activeHistoryId) return;

    // Append a separator so previous logs stay visible
    const ts = new Date().toLocaleTimeString("en-US", {
      timeZone: "America/Los_Angeles",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    });
    accumulatedLogsRef.current = [
      ...accumulatedLogsRef.current,
      "",
      `── Operation ${activeHistoryId}  ${ts} PST ──────────────────────`,
    ];
    lastOpLogCountRef.current = 0;
    savePersistedLogs(accumulatedLogsRef.current);
    setLiveLogs([...accumulatedLogsRef.current]);

    livePollRef.current = window.setInterval(async () => {
      try {
        const data = await fetchFlashLogs(activeHistoryId);
        const allLines: string[] = data.logs ?? [];
        const newLines = allLines.slice(lastOpLogCountRef.current);
        if (newLines.length > 0) {
          accumulatedLogsRef.current = [...accumulatedLogsRef.current, ...newLines];
          lastOpLogCountRef.current = allLines.length;
          savePersistedLogs(accumulatedLogsRef.current);
          setLiveLogs([...accumulatedLogsRef.current]);
        }
        // Once terminal status arrives, do one final refresh then stop
        if (data.status === "success" || data.status === "failed") {
          if (livePollRef.current !== null) {
            window.clearInterval(livePollRef.current);
            livePollRef.current = null;
          }
          await refreshData();
        }
      } catch {
        // ignore transient errors
      }
    }, 400);

    return () => {
      if (livePollRef.current !== null) {
        window.clearInterval(livePollRef.current);
        livePollRef.current = null;
      }
    };
  }, [activeHistoryId, refreshData]);

  // Instant cross-tab/cross-window sync via BroadcastChannel
  useEffect(() => {
    const unsub = subscribeToBroadcast((newState) => {
      setVcuState(newState);
      refreshData().catch(() => {});
    });
    return unsub;
  }, [setVcuState, refreshData]);

  const executeAction = useCallback(async (action: () => Promise<unknown>) => {
    // Only block if actively flashing - bootloading is fine for Flash Binary
    const liveState = await fetchVcuState();
    if (liveState?.state === "flashing") {
      setErrorMessage("VCU is currently flashing. Wait for it to finish.");
      return;
    }

    setIsBusy(true);
    setErrorMessage("");

    try {
      const result = await action();
      // Capture historyId if the action returned one
      const historyId = (result as { historyId?: string } | null)?.historyId;
      if (historyId) {
        activeHistoryIdRef.current = historyId;
        setActiveHistoryId(historyId);
      }
      await refreshData();
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Action failed";
      setErrorMessage(message);
    } finally {
      setIsBusy(false);
    }
  }, [refreshData]);

  const handleBoot = () => executeAction(() => bootloadOnly());
  const handleBootAndFlash = (formData: FormData) => executeAction(() => bootAndFlash(formData));
  const handleFlashOnly = (formData: FormData) => executeAction(() => flashOnly(formData));
  const handleUpdateHistoryNotes = async (entryId: string, notes: string) => {
    await updateFlashHistoryNotes(entryId, notes);
    await refreshData();
  };
  const handleSelectStoredHex = (fileId: string) => getStoredHexFile(fileId);

  return {
    vcuState,
    history,
    isBusy,
    backendDown,
    errorMessage,
    powerCycleNeeded,
    liveLogs,
    activeHistoryId,
    handleBoot,
    handleBootAndFlash,
    handleFlashOnly,
    handleUpdateHistoryNotes,
    handleSelectStoredHex,
  };
}
