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

export function useVcuApp() {
  const { setVcuState, vcuState, setPowerCycleNeeded, powerCycleNeeded } = useVcuStore();
  
  const [history, setHistory] = useState<FlashHistoryEntry[]>([]);
  
  const [isBusy, setIsBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [backendDown, setBackendDown] = useState(false);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [liveLogs, setLiveLogs] = useState<string[]>([]);
  const livePollRef = useRef<number | null>(null);
  // Accumulated logs that persist across all operations for the lifetime of the page
  const accumulatedLogsRef = useRef<string[]>([]);
  // How many lines from the current operation we've already appended
  const lastOpLogCountRef = useRef<number>(0);

  const refreshData = useCallback(async () => {
    try {
      const [historyItems, statePayload] = await Promise.all([
        fetchFlashHistory(),
        fetchVcuState(),
      ]);

      setBackendDown(false);
      setHistory(Array.isArray(historyItems) ? historyItems : []);
      if (statePayload?.state) {
        setVcuState(statePayload.state);
      }
      setPowerCycleNeeded(statePayload?.powerCycle ?? false);
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
    if (livePollRef.current !== null) {
      window.clearInterval(livePollRef.current);
      livePollRef.current = null;
    }
    if (!activeHistoryId) return;

    // Append a separator so previous logs stay visible
    const ts = new Date().toLocaleTimeString();
    accumulatedLogsRef.current = [
      ...accumulatedLogsRef.current,
      "",
      `── Operation ${activeHistoryId}  ${ts} ──────────────────────`,
    ];
    lastOpLogCountRef.current = 0;
    setLiveLogs([...accumulatedLogsRef.current]);

    livePollRef.current = window.setInterval(async () => {
      try {
        const data = await fetchFlashLogs(activeHistoryId);
        const allLines: string[] = data.logs ?? [];
        const newLines = allLines.slice(lastOpLogCountRef.current);
        if (newLines.length > 0) {
          accumulatedLogsRef.current = [...accumulatedLogsRef.current, ...newLines];
          lastOpLogCountRef.current = allLines.length;
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
