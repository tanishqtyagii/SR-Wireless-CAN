import { useState, useCallback, useEffect } from "react";
import { useVcuStore } from "../store/vcuStore";
import {
  bootAndFlash,
  bootloadOnly,
  fetchFlashHistory,
  fetchVcuState,
  flashOnly,
  getStoredHexFile,
  pruneOrphanedRecords,
  updateFlashHistoryNotes,
} from "../services/api";
import { subscribeToBroadcast } from "../services/ws";
import { FlashHistoryEntry } from "../types";

export function useVcuApp() {
  const { setVcuState, vcuState } = useVcuStore();
  
  const [history, setHistory] = useState<FlashHistoryEntry[]>([]);
  
  const [isBusy, setIsBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [backendDown, setBackendDown] = useState(false);

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
      await action();
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
    handleBoot,
    handleBootAndFlash,
    handleFlashOnly,
    handleUpdateHistoryNotes,
    handleSelectStoredHex,
  };
}
