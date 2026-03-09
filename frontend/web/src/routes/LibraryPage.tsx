import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { Panel } from "../components/ui/Panel";
import { StatusPill } from "../components/ui/StatusPill";
import { fetchFlashHistory, fetchFlashLogs, fetchHexFiles, updateHexFileNotes } from "../services/api";
import { subscribeToBroadcast } from "../services/ws";
import { FlashHistoryEntry, HexFile } from "../types";

const DetailDrawer = lazy(() =>
  import("../components/ui/DetailDrawer").then((module) => ({ default: module.DetailDrawer }))
);

let _cachedFiles: HexFile[] = [];
const _cachedHistoryByFile = new Map<string, FlashHistoryEntry[]>();
const _cachedLogsByHistory = new Map<string, string[]>();
let _filesPrefetch: Promise<HexFile[]> | null = null;

let _persistedFile: HexFile | null = null;
let _persistedNotes = "";

function prefetchFiles(): Promise<HexFile[]> {
  if (!_filesPrefetch) {
    _filesPrefetch = fetchHexFiles()
      .then((data) => {
        _cachedFiles = Array.isArray(data) ? data : [];
        return _cachedFiles;
      })
      .catch(() => _cachedFiles);
  }
  return _filesPrefetch;
}

void prefetchFiles();

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("en-US", {
    timeZone: "America/Los_Angeles",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatDateTime(iso: string | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-US", {
    timeZone: "America/Los_Angeles",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function attachCachedLogs(entries: FlashHistoryEntry[]): FlashHistoryEntry[] {
  return entries.map((entry) => {
    const logs = _cachedLogsByHistory.get(entry.id);
    return logs ? { ...entry, logs } : entry;
  });
}

export default function LibraryPage() {
  const [files, setFiles] = useState<HexFile[]>(_cachedFiles);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<HexFile | null>(_persistedFile);
  const [drawerNotes, setDrawerNotes] = useState(_persistedNotes);
  const [savingNotes, setSavingNotes] = useState(false);
  const [fileHistory, setFileHistory] = useState<FlashHistoryEntry[]>(
    _persistedFile ? attachCachedLogs(_cachedHistoryByFile.get(_persistedFile.id) ?? []) : []
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const [logLoadingId, setLogLoadingId] = useState<string | null>(null);
  const drawerNotesRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const loadFiles = (force = false) => {
    if (refreshInFlightRef.current && !force) {
      return refreshInFlightRef.current;
    }

    const task = fetchHexFiles()
      .then((data) => {
        const nextFiles = Array.isArray(data) ? data : [];
        _cachedFiles = nextFiles;
        _filesPrefetch = Promise.resolve(nextFiles);
        setFiles(nextFiles);
      })
      .catch(() => {})
      .finally(() => {
        refreshInFlightRef.current = null;
      });

    refreshInFlightRef.current = task;
    return task;
  };

  const loadFileHistory = async (fileId: string) => {
    setHistoryLoading(true);
    try {
      const entries = await fetchFlashHistory({ fileId, limit: 30 });
      const nextEntries = attachCachedLogs(Array.isArray(entries) ? entries : []);
      _cachedHistoryByFile.set(fileId, nextEntries);
      setFileHistory(nextEntries);
    } catch {
      setFileHistory(attachCachedLogs(_cachedHistoryByFile.get(fileId) ?? []));
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => {
    if (_cachedFiles.length > 0) {
      setFiles(_cachedFiles);
    } else {
      void prefetchFiles().then((items) => setFiles(items));
    }

    const poll = window.setInterval(() => {
      void loadFiles(true);
      if (_persistedFile) {
        void loadFileHistory(_persistedFile.id);
      }
    }, 3000);

    const unsub = subscribeToBroadcast(() => {
      void loadFiles(true);
      if (_persistedFile) {
        void loadFileHistory(_persistedFile.id);
      }
    });

    return () => {
      window.clearInterval(poll);
      unsub();
    };
  }, []);

  const refresh = () => {
    void loadFiles(true);
    if (selectedFile) {
      void loadFileHistory(selectedFile.id);
    }
  };

  const openDrawer = (file: HexFile) => {
    _persistedFile = file;
    _persistedNotes = file.notes ?? "";
    setSelectedFile(file);
    setDrawerNotes(file.notes ?? "");
    setFileHistory(attachCachedLogs(_cachedHistoryByFile.get(file.id) ?? []));
    void loadFileHistory(file.id);
  };

  const closeDrawer = () => {
    _persistedFile = null;
    _persistedNotes = "";
    setSelectedFile(null);
    setDrawerNotes("");
    setFileHistory([]);
  };

  const saveDrawerNotes = async () => {
    if (!selectedFile) return;
    setSavingNotes(true);
    try {
      const updated = await updateHexFileNotes(selectedFile.id, drawerNotes);
      _cachedFiles = _cachedFiles.map((file) =>
        file.id === selectedFile.id ? { ...file, notes: updated.notes } : file
      );
      setFiles(_cachedFiles);
      setSelectedFile((prev) => (prev ? { ...prev, notes: updated.notes } : null));
    } catch {
      // Ignore note-save failures in the drawer.
    } finally {
      setSavingNotes(false);
    }
  };

  const handleLoad = async (file: HexFile) => {
    setLoadingId(file.id);
    setLoadError(null);
    try {
      navigate("/", {
        state: {
          hexFileId: file.id,
          displayName: file.displayName || file.name,
          notes: file.notes || "",
        },
      });
    } catch {
      setLoadError(`"${file.displayName || file.name}" could not be loaded.`);
      setLoadingId(null);
    }
  };

  const handleDownload = (file: HexFile) => {
    const a = document.createElement("a");
    a.href = `/api/hex-files/${file.id}/content`;
    a.download = file.displayName || file.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const loadLogs = async (entryId: string) => {
    if (_cachedLogsByHistory.has(entryId)) {
      setFileHistory((prev) =>
        prev.map((entry) => (entry.id === entryId ? { ...entry, logs: _cachedLogsByHistory.get(entryId) } : entry))
      );
      return;
    }

    setLogLoadingId(entryId);
    try {
      const data = await fetchFlashLogs(entryId);
      const logs = data.logs ?? [];
      _cachedLogsByHistory.set(entryId, logs);
      setFileHistory((prev) => prev.map((entry) => (entry.id === entryId ? { ...entry, logs } : entry)));
    } catch {
      // Ignore per-entry log failures.
    } finally {
      setLogLoadingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-theme-bg text-theme-text font-sans flex flex-col p-4 gap-4 h-screen overflow-hidden">
      <header className="flex justify-between items-center shrink-0">
        <div className="flex items-center gap-4">
          <img src="/branding/spartan-logo.png" alt="Spartan Racing" className="h-10 w-auto object-contain" />
          <nav className="flex items-center gap-1 border border-theme-border bg-theme-panel rounded-full p-1">
            <button
              onClick={() => navigate("/")}
              className={`px-4 py-1 text-xs font-bold tracking-wide rounded-full transition-colors ${
                location.pathname === "/"
                  ? "bg-theme-text text-theme-bg"
                  : "text-theme-text-muted hover:text-theme-text"
              }`}
            >
              FLASH
            </button>
            <button
              onClick={() => navigate("/library")}
              className={`px-4 py-1 text-xs font-bold tracking-wide rounded-full transition-colors ${
                location.pathname === "/library"
                  ? "bg-theme-text text-theme-bg"
                  : "text-theme-text-muted hover:text-theme-text"
              }`}
            >
              LIBRARY
            </button>
          </nav>
        </div>
        <button
          onClick={refresh}
          className="text-xs text-theme-text-muted hover:text-theme-text transition-colors border border-theme-border px-2.5 py-1.5 rounded-full"
          title="Refresh"
        >
          Refresh
        </button>
      </header>

      <div className="flex-1 min-h-0 overflow-hidden">
        <Panel className="h-full bg-theme-panel border border-theme-border shadow-sm flex flex-col">
          <div className="shrink-0 px-6 pt-5 pb-4 border-b border-theme-border flex items-center justify-between">
            <div>
              <h2 className="text-sm font-bold text-theme-text tracking-wide">HEX FILE LIBRARY</h2>
            </div>
            <span className="text-xs text-theme-text-muted">{files.length} file{files.length !== 1 ? "s" : ""}</span>
          </div>

          {loadError && (
            <div className="mx-6 mt-4 px-4 py-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded">
              {loadError}
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto [content-visibility:auto]">
            {files.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center gap-2">
                <svg className="w-10 h-10 text-theme-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                </svg>
                <p className="text-sm text-theme-text-muted">No hex files uploaded yet</p>
              </div>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-theme-border">
                    <th className="px-6 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">FILE</th>
                    <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">SIZE</th>
                    <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">UPLOADED</th>
                    <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">LAST FLASHED</th>
                    <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">STATUS</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {files.map((file, i) => (
                    <tr
                      key={file.id}
                      className={`border-b border-theme-border last:border-b-0 hover:bg-theme-bg/50 transition-colors ${
                        i % 2 === 0 ? "" : "bg-theme-bg/20"
                      }`}
                    >
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 bg-theme-bg border border-theme-border rounded flex items-center justify-center shrink-0">
                            <svg className="w-4 h-4 text-theme-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                            </svg>
                          </div>
                          <div className="min-w-0">
                            <button
                              className="font-semibold text-theme-text hover:underline underline-offset-2 truncate max-w-[240px] text-left block"
                              onClick={() => openDrawer(file)}
                            >
                              {file.displayName || file.name}
                            </button>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4 text-sm text-theme-text-muted whitespace-nowrap">{formatSize(file.size)}</td>
                      <td
                        className="px-4 py-4 text-sm text-theme-text-muted whitespace-nowrap"
                        title={file.uploadedAt ? `${new Date(file.uploadedAt).toLocaleString("en-US", { timeZone: "America/Los_Angeles" })} PST` : undefined}
                      >
                        {formatDate(file.uploadedAt)}
                      </td>
                      <td
                        className="px-4 py-4 text-sm text-theme-text-muted whitespace-nowrap"
                        title={file.lastFlashedAt ? `${new Date(file.lastFlashedAt).toLocaleString("en-US", { timeZone: "America/Los_Angeles" })} PST` : undefined}
                      >
                        {formatDate(file.lastFlashedAt)}
                      </td>
                      <td className="px-4 py-4">
                        <StatusPill status={file.status} size="sm" />
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2 justify-end">
                          <button
                            onClick={() => handleDownload(file)}
                            className="p-1.5 text-theme-text-muted hover:text-theme-text border border-theme-border rounded hover:bg-theme-bg transition-colors"
                            title="Download .hex"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                            </svg>
                          </button>
                          <Button
                            variant="secondary"
                            className="px-3 py-1 text-xs font-semibold"
                            onClick={() => handleLoad(file)}
                            isLoading={loadingId === file.id}
                            disabled={loadingId !== null}
                          >
                            Load
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Panel>
      </div>

      <Suspense fallback={null}>
        <DetailDrawer
          isOpen={!!selectedFile}
          onClose={closeDrawer}
          title={selectedFile ? (selectedFile.displayName || selectedFile.name) : ""}
        >
          {selectedFile && (
            <div className="flex flex-col gap-5">
              <div className="flex flex-col gap-1.5">
                {selectedFile.displayName && selectedFile.displayName !== selectedFile.name && (
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-xs text-theme-text-muted shrink-0">Original</span>
                    <span className="text-xs text-theme-text-muted font-mono truncate">{selectedFile.name}</span>
                  </div>
                )}
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-theme-text-muted shrink-0">ID</span>
                  <span className="text-xs font-mono text-theme-text-muted">{selectedFile.id}</span>
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-theme-text-muted shrink-0">Size</span>
                  <span className="text-xs text-theme-text">{formatSize(selectedFile.size)}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-theme-text-muted shrink-0">Status</span>
                  <StatusPill status={selectedFile.status} size="sm" />
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-theme-text-muted shrink-0">Uploaded</span>
                  <span className="text-xs text-theme-text">{formatDateTime(selectedFile.uploadedAt)}</span>
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-theme-text-muted shrink-0">Last flashed</span>
                  <span className="text-xs text-theme-text">{formatDateTime(selectedFile.lastFlashedAt)}</span>
                </div>
                {selectedFile.lastFlashedBy && (
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-xs text-theme-text-muted shrink-0">Last flashed by</span>
                    <span className="text-xs text-theme-text">{selectedFile.lastFlashedBy}</span>
                  </div>
                )}
              </div>

              <div className="border-t border-theme-border" />

              <div className="flex flex-col gap-2">
                <label htmlFor="drawer-notes" className="text-xs font-bold text-theme-text-muted tracking-wider">NOTES</label>
                <textarea
                  id="drawer-notes"
                  ref={drawerNotesRef}
                  value={drawerNotes}
                  onChange={(e) => setDrawerNotes(e.target.value)}
                  rows={3}
                  className="w-full bg-theme-bg border border-theme-border text-theme-text text-sm px-3 py-2 rounded resize-none focus:outline-none focus:ring-1 focus:ring-theme-primary placeholder:text-theme-text-muted placeholder:opacity-40"
                  placeholder="Add notes..."
                />
                <Button
                  variant="secondary"
                  className="self-end px-3 py-1 text-xs font-semibold"
                  onClick={saveDrawerNotes}
                  isLoading={savingNotes}
                >
                  Save
                </Button>
              </div>

              <div className="border-t border-theme-border" />

              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-bold text-theme-text-muted tracking-wider">FLASH LOG</p>
                  {historyLoading && <span className="text-[11px] text-theme-text-muted">Refreshing...</span>}
                </div>
                {fileHistory.length === 0 ? (
                  <div className="rounded border border-theme-border bg-theme-bg px-3 py-2 text-xs text-theme-text-muted">
                    No flash history for this file yet.
                  </div>
                ) : (
                  fileHistory.map((entry) => (
                    <div key={entry.id} className="flex flex-col gap-2 rounded border border-theme-border bg-theme-bg p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs text-theme-text-muted">
                          <span className="font-mono text-theme-text">{entry.id}</span>
                          <span className="mx-1.5 opacity-50">&middot;</span>
                          {new Date(entry.timestamp).toLocaleString("en-US", {
                            timeZone: "America/Los_Angeles",
                            month: "short",
                            day: "numeric",
                            hour: "numeric",
                            minute: "2-digit",
                          })} PST
                          {entry.operator && <span className="ml-1.5 opacity-70">&middot; {entry.operator}</span>}
                        </span>
                        <StatusPill status={entry.status} size="sm" />
                      </div>
                      {entry.logs ? (
                        <div className="max-h-32 overflow-y-auto rounded border border-theme-border bg-theme-panel p-2">
                          {entry.logs.map((line, index) => (
                            <div
                              key={`${entry.id}-${index}`}
                              className="text-xs font-mono text-theme-text-muted leading-relaxed whitespace-pre-wrap"
                            >
                              {line}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <Button
                          variant="secondary"
                          className="self-start px-2.5 py-1 text-[11px] font-semibold"
                          onClick={() => void loadLogs(entry.id)}
                          isLoading={logLoadingId === entry.id}
                        >
                          Load logs
                        </Button>
                      )}
                    </div>
                  ))
                )}
              </div>

              <div className="border-t border-theme-border" />

              <div className="flex items-center gap-3">
                <Button
                  variant="primary"
                  className="flex-1 text-xs font-semibold"
                  onClick={() => handleLoad(selectedFile)}
                  isLoading={loadingId === selectedFile.id}
                  disabled={loadingId !== null}
                >
                  Load into Flash
                </Button>
                <button
                  onClick={() => handleDownload(selectedFile)}
                  className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-theme-text-muted hover:text-theme-text border border-theme-border rounded hover:bg-theme-bg transition-colors"
                  title="Download .hex"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                  </svg>
                  Download
                </button>
              </div>
            </div>
          )}
        </DetailDrawer>
      </Suspense>
    </div>
  );
}
