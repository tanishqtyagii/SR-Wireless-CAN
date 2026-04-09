import { useCallback, useEffect, useRef, useState } from "react";
import { useVcuStore } from "../store/vcuStore";
import {
  bootAndFlash,
  bootloadOnly,
  confirmImd,
  fetchFlashHistory,
  fetchFlashLogs,
  fetchVcuState,
  flashOnly,
  getStoredHexFile,
  pruneOrphanedRecords,
  updateFlashHistoryNotes,
} from "../services/api";
import { hydrateFlashHistoryLogs, setCachedFlashLogs } from "../services/flashLogCache";
import { subscribeToBroadcast } from "../services/ws";
import { FlashHistoryEntry } from "../types";

const HISTORY_STORAGE_KEY = "sr_flash_history";
const LOGS_STORAGE_KEY = "sr_flash_logs";
const HISTORY_LIMIT = 120;
const LOGS_MAX_LINES = 1200;

function loadPersistedHistory(): FlashHistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function savePersistedHistory(items: FlashHistoryEntry[]): void {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(items));
  } catch {
    // Ignore quota failures.
  }
}

function loadPersistedLogs(): string[] {
  try {
    return JSON.parse(localStorage.getItem(LOGS_STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function savePersistedLogs(lines: string[]): string[] {
  const capped = lines.length > LOGS_MAX_LINES ? lines.slice(lines.length - LOGS_MAX_LINES) : lines;
  try {
    localStorage.setItem(LOGS_STORAGE_KEY, JSON.stringify(capped));
  } catch {
    // Ignore quota failures.
  }
  return capped;
}

let _cachedHistory: FlashHistoryEntry[] = loadPersistedHistory();
let _persistedLogs: string[] = loadPersistedLogs();

export function useVcuApp() {
  const vcuState = useVcuStore((state) => state.vcuState);
  const setVcuState = useVcuStore((state) => state.setVcuState);
  const powerCycleNeeded = useVcuStore((state) => state.powerCycleNeeded);
  const setPowerCycleNeeded = useVcuStore((state) => state.setPowerCycleNeeded);
  const imdWaiting = useVcuStore((state) => state.imdWaiting);
  const setImdWaiting = useVcuStore((state) => state.setImdWaiting);

  const [history, setHistory] = useState<FlashHistoryEntry[]>(_cachedHistory);
  const [isBusy, setIsBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [backendDown, setBackendDown] = useState(false);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [liveLogs, setLiveLogs] = useState<string[]>(_persistedLogs);

  const livePollRef = useRef<number | null>(null);
  const accumulatedLogsRef = useRef<string[]>(_persistedLogs);
  const lastOpLogCountRef = useRef(0);
  const activeHistoryIdRef = useRef<string | null>(null);
  const activeOperationLabelRef = useRef<string>("");
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const refreshData = useCallback(() => {
    if (refreshInFlightRef.current) {
      return refreshInFlightRef.current;
    }

    const task = (async () => {
      try {
        const [historyItems, statePayload] = await Promise.all([
          fetchFlashHistory({ limit: HISTORY_LIMIT, includeLogs: true }),
          fetchVcuState(),
        ]);

        setBackendDown(false);
        const items = hydrateFlashHistoryLogs(Array.isArray(historyItems) ? historyItems : []);
        _cachedHistory = items;
        savePersistedHistory(items);
        setHistory(items);

        if (statePayload?.state) {
          setVcuState(statePayload.state);
        }
        setPowerCycleNeeded(statePayload?.powerCycle ?? false);
        setImdWaiting(statePayload?.imdWaiting ?? false);

        if (activeHistoryIdRef.current) {
          const activeEntry = items.find((entry) => entry.id === activeHistoryIdRef.current);
          if (activeEntry && (activeEntry.status === "success" || activeEntry.status === "failed")) {
            activeHistoryIdRef.current = null;
            setActiveHistoryId(null);
          }
        }
      } catch (err: unknown) {
        const isNetworkError = err instanceof TypeError;
        const isProxyError = err instanceof Error && /^HTTP (5\d{2})$/.test(err.message);
        if (isNetworkError || isProxyError) {
          setBackendDown(true);
          return;
        }

        const message = err instanceof Error ? err.message : "Failed to fetch data.";
        setErrorMessage(message);
      } finally {
        refreshInFlightRef.current = null;
      }
    })();

    refreshInFlightRef.current = task;
    return task;
  }, [setPowerCycleNeeded, setVcuState]);

  useEffect(() => {
    pruneOrphanedRecords().finally(() => {
      void refreshData();
    });

    const pollHandle = window.setInterval(() => {
      void refreshData();
    }, 3000);

    return () => window.clearInterval(pollHandle);
  }, [refreshData]);

  useEffect(() => {
    activeHistoryIdRef.current = activeHistoryId;
    if (livePollRef.current !== null) {
      window.clearInterval(livePollRef.current);
      livePollRef.current = null;
    }
    if (!activeHistoryId) return;

    const ts = new Date().toLocaleTimeString("en-US", {
      timeZone: "America/Los_Angeles",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    });
    const nextLogs = savePersistedLogs([
      ...accumulatedLogsRef.current,
      "",
      `${activeOperationLabelRef.current || "Operation started"}  ${ts} PST`,
    ]);
    accumulatedLogsRef.current = nextLogs;
    _persistedLogs = nextLogs;
    lastOpLogCountRef.current = 0;
    setLiveLogs(nextLogs);

    livePollRef.current = window.setInterval(async () => {
      try {
        const data = await fetchFlashLogs(activeHistoryId);
        const allLines: string[] = data.logs ?? [];
        setCachedFlashLogs(activeHistoryId, allLines);
        const newLines = allLines.slice(lastOpLogCountRef.current);
        if (newLines.length > 0) {
          const merged = savePersistedLogs([...accumulatedLogsRef.current, ...newLines]);
          accumulatedLogsRef.current = merged;
          _persistedLogs = merged;
          lastOpLogCountRef.current = allLines.length;
          setLiveLogs(merged);
        }

        if (data.status === "success" || data.status === "failed") {
          if (livePollRef.current !== null) {
            window.clearInterval(livePollRef.current);
            livePollRef.current = null;
          }
          activeHistoryIdRef.current = null;
          activeOperationLabelRef.current = "";
          setActiveHistoryId(null);
          await refreshData();
        }
      } catch {
        // Ignore transient fetch errors while an operation is running.
      }
    }, 400);

    return () => {
      if (livePollRef.current !== null) {
        window.clearInterval(livePollRef.current);
        livePollRef.current = null;
      }
    };
  }, [activeHistoryId, refreshData]);

  useEffect(() => {
    const unsub = subscribeToBroadcast((newState) => {
      setVcuState(newState);
      void refreshData();
    });
    return unsub;
  }, [refreshData, setVcuState]);

  const executeAction = useCallback(async (action: () => Promise<unknown>, operationLabel?: string) => {
    const liveState = await fetchVcuState();
    if (liveState?.state === "flashing") {
      setErrorMessage("VCU is currently flashing. Wait for it to finish.");
      return;
    }

    setIsBusy(true);
    setErrorMessage("");

    try {
      const result = await action();
      const historyId = (result as { historyId?: string } | null)?.historyId;
      if (historyId) {
        activeHistoryIdRef.current = historyId;
        activeOperationLabelRef.current = operationLabel?.trim() || "Operation started";
        setActiveHistoryId(historyId);
      }
      await refreshData();
    } catch (error: unknown) {
      activeOperationLabelRef.current = "";
      const message = error instanceof Error ? error.message : "Action failed";
      setErrorMessage(message);
    } finally {
      setIsBusy(false);
    }
  }, [refreshData]);

  const handleBoot = () => executeAction(() => bootloadOnly(), "Bootload");
  const handleBootAndFlash = (formData: FormData, operationLabel?: string) =>
    executeAction(() => bootAndFlash(formData), operationLabel);
  const handleFlashOnly = (formData: FormData, operationLabel?: string) =>
    executeAction(() => flashOnly(formData), operationLabel);
  const handleUpdateHistoryNotes = async (entryId: string, notes: string) => {
    await updateFlashHistoryNotes(entryId, notes);
    await refreshData();
  };
  const handleSelectStoredHex = (fileId: string) => getStoredHexFile(fileId);
  const handleImdConfirm = async () => {
    await confirmImd();
    await refreshData();
  };

  return {
    vcuState,
    history,
    isBusy,
    backendDown,
    errorMessage,
    powerCycleNeeded,
    imdWaiting,
    liveLogs,
    handleBoot,
    handleBootAndFlash,
    handleFlashOnly,
    handleUpdateHistoryNotes,
    handleSelectStoredHex,
    handleImdConfirm,
  };
}
