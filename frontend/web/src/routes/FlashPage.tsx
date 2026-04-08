import { DragEvent, Suspense, lazy, useEffect, useReducer, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useVcuApp } from "../hooks/useVcuApp";
import { clearAllData, fetchFlashLogs, getStoredHexFile } from "../services/api";
import { Button } from "../components/ui/Button";
import { Dropzone } from "../components/ui/Dropzone";
import { StatusPill } from "../components/ui/StatusPill";
import { NameModal } from "../components/ui/NameModal";
import { FlashHistoryEntry } from "../types";
import { Panel, PanelHeader, PanelTitle, PanelContent } from "../components/ui/Panel";
import { InlineAlert } from "../components/ui/InlineAlert";

const LogModal = lazy(() =>
  import("../components/flash/LogModal").then((module) => ({ default: module.LogModal }))
);
const FlashDrawer = lazy(() =>
  import("../components/flash/FlashDrawer").then((module) => ({ default: module.FlashDrawer }))
);

const STORED_HEX_DRAG_TYPE = "application/x-sr-hex-id";
const OPERATOR_KEY = "sr_operator_name";

// Module-level persistence so tab switches don't reset the loaded file
let _persistedFile: File | null = null;
let _persistedDisplayName = "";
let _persistedNotes = "";
let _persistedStoredId: string | null = null;

// ── File state reducer (replaces 4 useState calls) ──────────────────────────
type FileState = { file: File | null; displayName: string; notes: string; storedFileId: string | null };
type FileAction =
  | { type: "LOAD"; file: File; displayName: string; notes: string; storedFileId: string | null }
  | { type: "SET_NAME"; name: string }
  | { type: "SET_NOTES"; notes: string }
  | { type: "CLEAR" };
function fileReducer(state: FileState, action: FileAction): FileState {
  switch (action.type) {
    case "LOAD":     return { file: action.file, displayName: action.displayName, notes: action.notes, storedFileId: action.storedFileId };
    case "SET_NAME": return { ...state, displayName: action.name };
    case "SET_NOTES":return { ...state, notes: action.notes };
    case "CLEAR":    return { file: null, displayName: "", notes: "", storedFileId: null };
  }
}

// ── Drag / UI state reducer (replaces 3 useState calls) ─────────────────────
type DragState = { dragError: string; isDragReplace: boolean; replaceNotice: string };
type DragAction =
  | { type: "DRAG_OVER" }
  | { type: "DRAG_LEAVE" }
  | { type: "SET_ERROR"; error: string }
  | { type: "REPLACED"; name: string }
  | { type: "CLEAR_NOTICE" };
function dragReducer(state: DragState, action: DragAction): DragState {
  switch (action.type) {
    case "DRAG_OVER":    return { ...state, isDragReplace: true };
    case "DRAG_LEAVE":   return { ...state, isDragReplace: false };
    case "SET_ERROR":    return { ...state, dragError: action.error };
    case "REPLACED":     return { ...state, isDragReplace: false, replaceNotice: `Replaced with "${action.name}"` };
    case "CLEAR_NOTICE": return { ...state, replaceNotice: "" };
  }
}

function formatRecentFlashTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  const datePart = date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
  const timePart = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `${datePart} ${timePart}`;
}

export default function FlashPage() {
  const {
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
  } = useVcuApp();
  
  const [fileState, dispatchFile] = useReducer(fileReducer, {
    file: _persistedFile,
    displayName: _persistedDisplayName,
    notes: _persistedNotes,
    storedFileId: _persistedStoredId,
  });
  const [dragState, dispatchDrag] = useReducer(dragReducer, {
    dragError: "", isDragReplace: false, replaceNotice: "",
  });
  const [operatorName, setOperatorName] = useState<string>(
    () => localStorage.getItem(OPERATOR_KEY) ?? ""
  );
  const [logOpen, setLogOpen] = useState(false);
  const [drawerItem, setDrawerItem] = useState<FlashHistoryEntry | null>(null);
  const [drawerLogs, setDrawerLogs] = useState<string[] | undefined>(undefined);
  const replaceNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  // Auto-open log when an operation starts
  useEffect(() => {
    if (vcuState === "bootloading" || vcuState === "flashing") setLogOpen(true);
  }, [vcuState]);

  useEffect(() => {
    if (!drawerItem) {
      setDrawerLogs(undefined);
      return;
    }

    let cancelled = false;
    fetchFlashLogs(drawerItem.id)
      .then((data) => {
        if (!cancelled) setDrawerLogs(data.logs ?? []);
      })
      .catch(() => {
        if (!cancelled) setDrawerLogs([]);
      });

    return () => {
      cancelled = true;
    };
  }, [drawerItem]);

  // Auto-load file navigated from Library page — single dispatch replaces 4 setStates
  useEffect(() => {
    const state = location.state as { hexFileId?: string; displayName?: string; notes?: string } | null;
    if (!state?.hexFileId) return;
    window.history.replaceState({}, "");
    getStoredHexFile(state.hexFileId).then((file) => {
      const dn = state.displayName || file.name;
      const n  = state.notes || "";
      _persistedFile = file; _persistedDisplayName = dn; _persistedNotes = n; _persistedStoredId = state.hexFileId!;
      dispatchFile({ type: "LOAD", file, displayName: dn, notes: n, storedFileId: state.hexFileId! });
    }).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clearFile = () => {
    _persistedFile = null; _persistedDisplayName = ""; _persistedNotes = ""; _persistedStoredId = null;
    dispatchFile({ type: "CLEAR" });
    dispatchDrag({ type: "CLEAR_NOTICE" });
  };

  const replaceFile = (file: File, id: string | null = null) => {
    _persistedFile = file; _persistedDisplayName = file.name; _persistedNotes = ""; _persistedStoredId = id;
    dispatchFile({ type: "LOAD", file, displayName: file.name, notes: "", storedFileId: id });
    dispatchDrag({ type: "REPLACED", name: file.name });
    if (replaceNoticeTimer.current) clearTimeout(replaceNoticeTimer.current);
    replaceNoticeTimer.current = setTimeout(() => dispatchDrag({ type: "CLEAR_NOTICE" }), 3000);
  };

  const onAction = async (actionFn: (fd: FormData) => Promise<void>) => {
    if (!fileState.file) return;
    const formData = new FormData();
    formData.append("file", fileState.file);
    formData.append("displayName", fileState.displayName || fileState.file.name);
    formData.append("notes", fileState.notes);
    formData.append("operator", operatorName);
    await actionFn(formData);
    clearFile();
  };

  const onStoredFileDrop = async (fileId: string) => {
    try {
      const droppedFile = await handleSelectStoredHex(fileId);
      if (fileState.file) {
        replaceFile(droppedFile, fileId);
      } else {
        _persistedFile = droppedFile; _persistedDisplayName = droppedFile.name; _persistedNotes = ""; _persistedStoredId = fileId;
        dispatchFile({ type: "LOAD", file: droppedFile, displayName: droppedFile.name, notes: "", storedFileId: fileId });
      }
      dispatchDrag({ type: "SET_ERROR", error: "" });
    } catch {
      dispatchDrag({ type: "SET_ERROR", error: "This flash record has no stored HEX payload to drag." });
    }
  };

  const handlePanelDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    dispatchDrag({ type: "DRAG_OVER" });
  };

  const handlePanelDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node)) dispatchDrag({ type: "DRAG_LEAVE" });
  };

  const handlePanelDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dispatchDrag({ type: "DRAG_LEAVE" });
    const storedId = event.dataTransfer.getData(STORED_HEX_DRAG_TYPE);
    if (storedId) { await onStoredFileDrop(storedId); return; }
    const droppedFile = event.dataTransfer.files?.[0];
    if (droppedFile) replaceFile(droppedFile);
  };

  const startHistoryDrag = (event: DragEvent<HTMLLIElement>, entry: FlashHistoryEntry) => {
    event.dataTransfer.setData(STORED_HEX_DRAG_TYPE, entry.fileId ?? "");
    event.dataTransfer.effectAllowed = "copy";
  };

  const isEnteringBootloader = vcuState === "bootloading";
  const isBootloaded           = vcuState === "bootloaded";
  const vcuIsBusy              = vcuState === "flashing";
  const { file, displayName, notes, storedFileId } = fileState;
  const { dragError, isDragReplace, replaceNotice } = dragState;
  const canFlash = !!file && !isBusy && !vcuIsBusy;

  return (
    <div className="min-h-screen bg-theme-bg text-theme-text font-sans flex flex-col p-4 gap-4 h-screen overflow-hidden">
      {/* Power-cycle banner */}
      {powerCycleNeeded && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[60] flex items-center gap-3 px-5 py-3 rounded-lg border border-red-500 bg-theme-bg shadow-lg">
          <span className="w-2 h-2 rounded-full bg-red-500 shrink-0" />
          <span className="text-sm font-bold text-red-400 uppercase tracking-widest">Power cycle the car</span>
        </div>
      )}

      {/* IMD confirmation banner */}
      {imdWaiting && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[60] flex items-center gap-4 px-5 py-3 rounded-lg border border-yellow-500 bg-theme-bg shadow-lg">
          <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse shrink-0" />
          <span className="text-sm font-bold text-yellow-400 uppercase tracking-widest">Press IMD button on car</span>
          <button
            onClick={handleImdConfirm}
            className="ml-2 px-4 py-1.5 rounded-md bg-yellow-500 hover:bg-yellow-400 text-black text-xs font-bold uppercase tracking-wide transition-colors"
          >
            IMD OK
          </button>
        </div>
      )}

      {!operatorName && (
        <NameModal onConfirm={(name) => {
          localStorage.setItem(OPERATOR_KEY, name);
          setOperatorName(name);
        }} />
      )}
      {backendDown && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-theme-bg">
          <div className="flex flex-col items-center gap-4 max-w-sm text-center px-6 py-8 rounded-xl border border-red-500 bg-theme-panel shadow-xl">
            <svg className="w-10 h-10 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            <h2 className="text-lg font-bold text-red-500">Backend Offline</h2>
            <p className="text-sm text-theme-text-muted leading-relaxed">
              Cannot reach the Flask server at <span className="font-mono text-theme-text">localhost:5000</span>.<br />
              Start it with <span className="font-mono text-theme-text">python app.py</span> - the page will reconnect automatically.
            </p>
          </div>
        </div>
      )}
      
      {/* Header Row */}
      <header className="flex justify-between items-center shrink-0">
        <div className="flex items-center gap-4">
          <img src="/branding/spartan-logo.png" alt="Spartan Racing" className="h-10 w-auto object-contain" />
          <nav className="flex items-center gap-1 h-8 border border-theme-border bg-theme-panel rounded-full p-0.5">
            <button
              onClick={() => navigate("/")}
              className={`px-4 h-full flex items-center justify-center text-[11px] font-bold tracking-widest rounded-full transition-colors ${
                location.pathname === "/"
                  ? "bg-theme-text text-theme-bg shadow-sm"
                  : "text-theme-text-muted hover:text-theme-text"
              }`}
            >
              FLASH
            </button>
            <button
              onClick={() => navigate("/library")}
              className={`px-4 h-full flex items-center justify-center text-[11px] font-bold tracking-widest rounded-full transition-colors ${
                location.pathname === "/library"
                  ? "bg-theme-text text-theme-bg shadow-sm"
                  : "text-theme-text-muted hover:text-theme-text"
              }`}
            >
              LIBRARY
            </button>
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {operatorName && (
            <button
              onClick={() => {
                localStorage.removeItem(OPERATOR_KEY);
                setOperatorName("");
              }}
              title="Switch operator"
              className="flex items-center gap-2 h-8 px-3 border border-theme-border bg-theme-panel hover:bg-theme-panel-hover rounded-full transition-colors shadow-sm"
            >
              <span className="text-xs font-semibold text-theme-text">{operatorName}</span>
              <svg className="w-3 h-3 text-theme-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 9V5.25A2.25 2.25 0 0 1 10.5 3h6a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 16.5 21h-6a2.25 2.25 0 0 1-2.25-2.25V15" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12H3m0 0 3.75-3.75M3 12l3.75 3.75" />
              </svg>
            </button>
          )}
          <div className="flex items-center gap-3 h-8 px-3 border border-theme-border bg-theme-panel rounded-full shadow-sm">
            <span className="text-xs font-semibold text-theme-text-muted tracking-wide">CURRENT STATE</span>
            <StatusPill status={vcuState} className="!border-none !bg-transparent !p-0 !text-xs" />
          </div>
          <button
            onClick={() => setLogOpen(true)}
            className="relative flex items-center gap-2 h-8 px-3 border border-theme-border bg-theme-panel hover:bg-theme-panel-hover rounded-full transition-colors shadow-sm"
            title="View operation log"
          >
            {/* Terminal icon */}
            <svg className="w-3.5 h-3.5 text-theme-text-muted shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0 0 21 18V6a2.25 2.25 0 0 0-2.25-2.25H5.25A2.25 2.25 0 0 0 3 6v12a2.25 2.25 0 0 0 2.25 2.25Z" />
            </svg>
            <span className="text-xs font-semibold text-theme-text">Log</span>
            {(vcuState === "flashing" || vcuState === "bootloading") && (
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse shrink-0" />
            )}
          </button>
          <button
            onClick={async () => {
              if (!window.confirm("Clear all stored files and flash history? This cannot be undone.")) return;
              await clearAllData();
              localStorage.removeItem("sr_flash_logs");
              localStorage.removeItem("sr_flash_history");
              clearFile();
              window.location.reload();
            }}
            className="h-8 px-3 flex items-center justify-center text-xs text-theme-text-muted hover:text-red-400 transition-colors border border-theme-border rounded-full bg-theme-panel shadow-sm hover:border-red-400/50"
            title="Clear all local data"
          >
            Clear DB
          </button>
        </div>
      </header>

      {/* Body Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0 w-full">
        <div className="lg:col-span-9 flex flex-col min-h-0">
          <Panel className="flex-1 min-h-0 bg-theme-panel border border-theme-border shadow-sm flex flex-col">
            {/* File / dropzone area */}
            <div className="flex-1 min-h-0 overflow-y-auto p-6">
              {!file ? (
                <div className="h-full flex items-center justify-center">
                  <Dropzone
                    onFileSelect={(f) => {
                      _persistedFile = f; _persistedDisplayName = f.name; _persistedNotes = ""; _persistedStoredId = null;
                      dispatchFile({ type: "LOAD", file: f, displayName: f.name, notes: "", storedFileId: null });
                    }}
                    onStoredFileDrop={onStoredFileDrop}
                    className="w-full h-full"
                  />
                </div>
              ) : (
                <div
                  className="h-full flex flex-col items-center justify-center w-full max-w-2xl mx-auto relative"
                  onDragOver={handlePanelDragOver}
                  onDragLeave={handlePanelDragLeave}
                  onDrop={handlePanelDrop}
                >
                  {/* Drag-replace overlay */}
                  {isDragReplace && (
                    <div className="absolute inset-0 z-10 rounded-lg border-2 border-dashed border-theme-primary bg-theme-primary/10 flex items-center justify-center pointer-events-none">
                      <div className="text-theme-primary font-semibold text-sm">Drop to replace current file</div>
                    </div>
                  )}

                  {/* Clear button */}
                  <button
                    onClick={clearFile}
                    className="absolute top-0 right-0 text-theme-text-muted hover:text-theme-text transition-colors p-1 rounded"
                    title="Clear file"
                    disabled={isBusy}
                  >
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>

                  <div className="w-16 h-16 bg-theme-bg border border-theme-border rounded-lg flex items-center justify-center mb-6">
                    <svg className="w-8 h-8 text-theme-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                    </svg>
                  </div>
                  
                  <input
                    type="text"
                    value={displayName}
                    onChange={(e) => { _persistedDisplayName = e.target.value; dispatchFile({ type: "SET_NAME", name: e.target.value }); }}
                    className="w-full bg-transparent text-xl font-bold text-theme-text text-center focus:outline-none focus:ring-2 focus:ring-theme-primary rounded px-2 py-1 border-b border-transparent hover:border-theme-border transition-colors mb-2"
                    placeholder="Filename"
                  />
                  {storedFileId && (
                    <div className="text-xs font-mono text-theme-text-muted mb-1">{storedFileId}</div>
                  )}
                  <div className="text-sm text-theme-text-muted mb-8">
                    {(file.size / 1024).toFixed(2)} KB • {new Date(file.lastModified).toLocaleDateString()}
                  </div>

                  {replaceNotice && (
                    <div className="mb-4 w-full">
                      <InlineAlert variant="warning" message={replaceNotice} />
                    </div>
                  )}

                  <div className="w-full space-y-2">
                    <label htmlFor="file-notes" className="text-xs font-bold text-theme-text-muted">Notes (Optional)</label>
                    <textarea
                      id="file-notes"
                      value={notes}
                      onChange={(e) => { _persistedNotes = e.target.value; dispatchFile({ type: "SET_NOTES", notes: e.target.value }); }}
                      className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-3 rounded focus:outline-none focus:ring-2 focus:ring-theme-primary min-h-[120px] resize-none text-sm"
                      placeholder="Add build notes or version info..."
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Execution area — flush to the bottom of the same card */}
            <div className="shrink-0 border-t border-theme-border p-4 flex flex-col gap-4">
              {errorMessage && <InlineAlert variant="error" message={errorMessage} />}
              {vcuIsBusy && !isBusy && (
                <InlineAlert variant="warning" message="VCU is currently busy. All operations are locked until it returns to idle." />
              )}

              <div className="grid grid-cols-2 gap-3">
                <Button 
                  variant="secondary" 
                  className="w-full py-4 font-semibold text-sm tracking-wide" 
                  onClick={handleBoot} 
                  disabled={isBusy || isEnteringBootloader || isBootloaded || vcuIsBusy}
                  isLoading={isBusy && vcuState === "bootloading" && !file}
                >
                  BOOTLOAD
                </Button>
                <Button 
                  variant="secondary" 
                  className="w-full py-4 font-semibold text-sm tracking-wide" 
                  onClick={() => canFlash && onAction(handleFlashOnly)} 
                  disabled={!canFlash || !isBootloaded || isBusy}
                  isLoading={isBusy && vcuState === "flashing"}
                >
                  FLASH FILE
                </Button>
              </div>

              <div className="flex items-center gap-3">
                <div className="flex-1 h-px bg-theme-border" />
                <span className="text-xs font-bold text-theme-text-muted">OR</span>
                <div className="flex-1 h-px bg-theme-border" />
              </div>

              <Button 
                variant="primary" 
                className="w-full py-4 font-semibold text-sm tracking-wide" 
                onClick={() => canFlash && onAction(handleBootAndFlash)} 
                disabled={!canFlash || isBusy}
                isLoading={isBusy && (vcuState === "bootloading" || vcuState === "bootloaded" || vcuState === "flashing")}
              >
                BOOTLOAD + FLASH FILE
              </Button>
            </div>
          </Panel>
        </div>

        {/* RIGHT COLUMN: History + Live Log */}
        <div className="lg:col-span-3 flex flex-col gap-4 min-h-0">
          <Panel className="flex-1 min-h-0 bg-theme-panel">
            <PanelHeader>
              <PanelTitle>Recently Flashed</PanelTitle>
            </PanelHeader>
            <PanelContent className="p-0">
              {dragError && (
                <div className="p-3 border-b border-theme-border">
                  <InlineAlert variant="error" message={dragError} />
                </div>
              )}
              <ul className="divide-y divide-theme-border">
                {history.filter(e => e.fileId).map(entry => (
                  <li 
                    key={entry.id} 
                    draggable
                    onDragStart={(event) => startHistoryDrag(event, entry)}
                    className="p-3 hover:bg-theme-panel-hover cursor-grab active:cursor-grabbing flex justify-between items-start"
                    title="Drag into upload area"
                    role="button"
                    tabIndex={0}
                    onClick={() => {
                      setDrawerLogs(undefined);
                      setDrawerItem(entry);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        setDrawerLogs(undefined);
                        setDrawerItem(entry);
                      }
                    }}
                  >
                    <div className="min-w-0 pr-2">
                      <div className="text-sm font-semibold text-theme-text truncate">{entry.name}</div>
                      <div className="text-xs text-theme-text-muted mt-0.5">
                        {entry.operator && <span className="mr-1.5">{entry.operator} &middot;</span>}
                        {formatRecentFlashTimestamp(entry.timestamp)}
                      </div>
                    </div>
                    <StatusPill status={entry.status} size="sm" className="shrink-0" />
                  </li>
                ))}
                {history.filter(e => e.fileId).length === 0 && (
                  <div className="p-6 text-center text-sm text-theme-text-muted">No flash history.</div>
                )}
              </ul>
            </PanelContent>
          </Panel>

        </div>

      </div>

      <Suspense fallback={null}>
        <LogModal
          isOpen={logOpen}
          onClose={() => setLogOpen(false)}
          logs={liveLogs}
          vcuState={vcuState}
        />
        <FlashDrawer
          item={drawerItem ? { ...drawerItem, logs: drawerLogs } : null}
          onClose={() => {
            setDrawerLogs(undefined);
            setDrawerItem(null);
          }}
          onSaveNotes={handleUpdateHistoryNotes}
        />
      </Suspense>
    </div>
  );
}
