
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { Panel } from "../components/ui/Panel";
import { StatusPill } from "../components/ui/StatusPill";
import { fetchFlashHistory, fetchFlashLogs, fetchHexFiles, updateHexFileNotes, updateFlashHistoryNotes } from "../services/api";
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

const OPERATOR_KEY = "sr_operator_name";
type SortMode = "newest" | "oldest" | "flashed" | "my-flashes" | "operator-asc" | "name-asc" | "name-desc";

const sortOptions: { value: SortMode; label: string }[] = [
  { value: "newest", label: "Recent Uploads" },
  { value: "oldest", label: "Oldest Uploads" },
  { value: "flashed", label: "Recently Flashed" },
  { value: "my-flashes", label: "Flashed By Me" },
  { value: "operator-asc", label: "Last Flashed By (A-Z)" },
  { value: "name-asc", label: "Name (A-Z)" },
  { value: "name-desc", label: "Name (Z-A)" },
];

function SortSelect({ value, onChange }: { value: SortMode; onChange: (v: SortMode) => void }) {
  const [isOpen, setIsOpen] = useState(false);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const currentOption = sortOptions.find((o) => o.value === value) || sortOptions[0];

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 h-8 border border-theme-border bg-theme-panel hover:bg-theme-panel-hover px-3 rounded-full transition-colors shadow-sm focus:outline-none"
      >
        <span className="text-xs font-semibold text-theme-text tracking-wide">{currentOption.label}</span>
        <svg
          className={`w-3.5 h-3.5 text-theme-text-muted transition-transform ${isOpen ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div 
          className="absolute top-full left-0 mt-2 min-w-[200px] bg-theme-panel border border-theme-border rounded-xl shadow-xl z-50 p-1.5 flex flex-col"
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {/* Animated Background Highlight */}
          {hoveredIndex !== null && (
            <div 
              className="absolute left-1.5 right-1.5 h-[32px] bg-theme-bg shadow-sm border border-theme-border/60 rounded-lg pointer-events-none transition-all duration-150 ease-out z-0"
              style={{ top: `${6 + hoveredIndex * 32}px` }} 
            />
          )}

          {sortOptions.map((opt, i) => {
            const isSelected = value === opt.value;
            const isHovered = hoveredIndex === i;
            return (
              <button
                key={opt.value}
                onMouseEnter={() => setHoveredIndex(i)}
                onFocus={() => setHoveredIndex(i)}
                onClick={() => {
                  onChange(opt.value);
                  setIsOpen(false);
                }}
                className={`flex items-center justify-between w-full text-left px-3 h-[32px] text-[11.5px] font-medium relative z-10 transition-colors rounded-lg ${
                  isSelected || isHovered ? "text-theme-text" : "text-theme-text-muted"
                }`}
              >
                <span>{opt.label}</span>
                {isSelected && (
                  <svg className="w-3.5 h-3.5 text-theme-text" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
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

  const [showAllFlashes, setShowAllFlashes] = useState(() => {
    return localStorage.getItem("libraryShowAllFlashes") === "true";
  });
  const [allFlashes, setAllFlashes] = useState<FlashHistoryEntry[]>([]);

  const visibleFlashes = useMemo(() => {
    return allFlashes.filter((entry) => entry.fileId);
  }, [allFlashes]);

  useEffect(() => {
    localStorage.setItem("libraryShowAllFlashes", String(showAllFlashes));
    if (showAllFlashes) {
      setHistoryLoading(true);
      fetchFlashHistory()
        .then((data) => setAllFlashes(Array.isArray(data) ? data : []))
        .catch(() => {})
        .finally(() => setHistoryLoading(false));
    }
  }, [showAllFlashes]);

  const [sortMode, setSortMode] = useState<SortMode>(() => {
    return (localStorage.getItem("librarySortMode") as SortMode) || "newest";
  });

  useEffect(() => {
    localStorage.setItem("librarySortMode", sortMode);
  }, [sortMode]);

  const sortedFiles = useMemo(() => {
    const list = [...files];
    const myName = localStorage.getItem(OPERATOR_KEY) || "";

    list.sort((a, b) => {
      if (sortMode === "newest") {
        return new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime();
      } else if (sortMode === "oldest") {
        return new Date(a.uploadedAt).getTime() - new Date(b.uploadedAt).getTime();
      } else if (sortMode === "flashed") {
        const tA = a.lastFlashedAt ? new Date(a.lastFlashedAt).getTime() : 0;
        const tB = b.lastFlashedAt ? new Date(b.lastFlashedAt).getTime() : 0;
        return tB - tA;
      } else if (sortMode === "my-flashes") {
        const aIsMine = a.lastFlashedBy === myName;
        const bIsMine = b.lastFlashedBy === myName;
        if (aIsMine && !bIsMine) return -1;
        if (!aIsMine && bIsMine) return 1;
        return new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime();
      } else if (sortMode === "operator-asc") {
        const opA = (a.lastFlashedBy || "").toLowerCase();
        const opB = (b.lastFlashedBy || "").toLowerCase();
        if (!opA && opB) return 1;
        if (opA && !opB) return -1;
        if (!opA && !opB) return new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime();
        return opA.localeCompare(opB);
      } else if (sortMode === "name-asc") {
        const nameA = (a.displayName || a.name).toLowerCase();
        const nameB = (b.displayName || b.name).toLowerCase();
        return nameA.localeCompare(nameB);
      } else if (sortMode === "name-desc") {
        const nameA = (a.displayName || a.name).toLowerCase();
        const nameB = (b.displayName || b.name).toLowerCase();
        return nameB.localeCompare(nameA);
      }
      return 0;
    });
    return list;
  }, [files, sortMode]);

  const sortedFlashes = useMemo(() => {
    const list = [...visibleFlashes];
    const myName = localStorage.getItem(OPERATOR_KEY) || "";

    list.sort((a, b) => {
      const timeA = new Date(a.timestamp).getTime();
      const timeB = new Date(b.timestamp).getTime();

      if (sortMode === "newest" || sortMode === "flashed") {
        return timeB - timeA;
      } else if (sortMode === "oldest") {
        return timeA - timeB;
      } else if (sortMode === "my-flashes") {
        const aIsMine = a.operator === myName;
        const bIsMine = b.operator === myName;
        if (aIsMine && !bIsMine) return -1;
        if (!aIsMine && bIsMine) return 1;
        return timeB - timeA;
      } else if (sortMode === "operator-asc") {
        const opA = (a.operator || "").toLowerCase();
        const opB = (b.operator || "").toLowerCase();
        if (!opA && opB) return 1;
        if (opA && !opB) return -1;
        if (!opA && !opB) return timeB - timeA;
        return opA.localeCompare(opB);
      } else if (sortMode === "name-asc") {
        return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
      } else if (sortMode === "name-desc") {
        return b.name.toLowerCase().localeCompare(a.name.toLowerCase());
      }
      return 0;
    });
    return list;
  }, [visibleFlashes, sortMode]);

  const latestFlashedFileId = useMemo(() => {
    return files.reduce((latest, current) => {
      if (!current.lastFlashedAt) return latest;
      if (!latest || new Date(current.lastFlashedAt).getTime() > new Date(latest.lastFlashedAt!).getTime()) {
        return current;
      }
      return latest;
    }, null as HexFile | null)?.id;
  }, [files]);

  const latestFlashId = useMemo(() => {
    return visibleFlashes.reduce((latest, current) => {
      if (!latest || new Date(current.timestamp).getTime() > new Date(latest.timestamp).getTime()) {
        return current;
      }
      return latest;
    }, null as FlashHistoryEntry | null)?.id;
  }, [visibleFlashes]);

  const loadFiles = (force = false) => {
    if (refreshInFlightRef.current && !force) {
      return refreshInFlightRef.current;
    }

    const tasks: Promise<any>[] = [
      fetchHexFiles()
        .then((data) => {
          const nextFiles = Array.isArray(data) ? data : [];
          _cachedFiles = nextFiles;
          _filesPrefetch = Promise.resolve(nextFiles);
          setFiles(nextFiles);
        })
        .catch(() => {})
    ];

    if (showAllFlashes) {
      tasks.push(
        fetchFlashHistory()
          .then((data) => setAllFlashes(Array.isArray(data) ? data : []))
          .catch(() => {})
      );
    }

    const task = Promise.all(tasks).finally(() => {
      refreshInFlightRef.current = null;
    }) as unknown as Promise<void>;

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
        <button
          onClick={refresh}
          className="h-8 px-3 flex items-center justify-center text-xs font-semibold text-theme-text-muted hover:text-theme-text transition-colors border border-theme-border rounded-full shadow-sm bg-theme-panel hover:bg-theme-panel-hover"
          title="Refresh"
        >
          Refresh
        </button>
      </header>

      <div className="flex-1 min-h-0 overflow-hidden">
        <Panel className="h-full bg-theme-panel border border-theme-border shadow-sm flex flex-col">
          <div className="shrink-0 px-6 pt-5 pb-4 border-b border-theme-border flex items-center justify-between">
            <div className="flex items-center gap-4">
              <h2 className="text-sm font-bold text-theme-text tracking-wide">HEX FILE LIBRARY</h2>
              <div className="flex items-center gap-4 border-l border-theme-border pl-6">
                <SortSelect value={sortMode} onChange={setSortMode} />
                <div className="flex items-center h-8 bg-theme-bg border border-theme-border rounded-full p-0.5 ml-2">
                  <button
                    onClick={() => setShowAllFlashes(false)}
                    className={`px-3 h-full flex items-center text-[10px] font-bold tracking-wider rounded-full transition-colors ${
                      !showAllFlashes
                        ? "bg-theme-text text-theme-bg shadow-sm"
                        : "text-theme-text-muted hover:text-theme-text"
                    }`}
                  >
                    GROUPED
                  </button>
                  <button
                    onClick={() => setShowAllFlashes(true)}
                    className={`px-3 h-full flex items-center text-[10px] font-bold tracking-wider rounded-full transition-colors ${
                      showAllFlashes
                        ? "bg-theme-text text-theme-bg shadow-sm"
                        : "text-theme-text-muted hover:text-theme-text"
                    }`}
                  >
                    ALL FLASHES
                  </button>
                </div>
              </div>
            </div>
            <span className="text-xs text-theme-text-muted">
              {showAllFlashes 
                ? `${sortedFlashes.length} flash${sortedFlashes.length !== 1 ? "es" : ""}`
                : `${sortedFiles.length} file${sortedFiles.length !== 1 ? "s" : ""}`
              }
            </span>
          </div>

          {loadError && (
            <div className="mx-6 mt-4 px-4 py-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded">
              {loadError}
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto [content-visibility:auto]">
            {(!showAllFlashes && sortedFiles.length === 0) || (showAllFlashes && sortedFlashes.length === 0) ? (
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
                    {!showAllFlashes && <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">SIZE</th>}
                    {!showAllFlashes && <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">UPLOADED</th>}
                    {showAllFlashes && <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">FLASHED BY</th>}
                    {showAllFlashes && <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">FLASHED AT</th>}
                    {!showAllFlashes && <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">LAST FLASHED</th>}
                    <th className="px-4 py-3 text-left text-xs font-bold text-theme-text-muted tracking-wider">STATUS</th>
                    <th className="px-6 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {showAllFlashes ? sortedFlashes.map((entry, i) => {
                    const isLatest = entry.id === latestFlashId;
                    return (
                    <tr
                      key={entry.id}
                      className={`border-b border-theme-border last:border-b-0 hover:bg-theme-bg/50 transition-colors ${
                        isLatest 
                          ? "bg-theme-primary/10 relative" 
                          : i % 2 === 0 ? "" : "bg-theme-bg/20"
                      }`}
                    >
                      <td className="px-6 py-4 relative">
                        {isLatest && <div className="absolute left-0 top-0 bottom-0 w-1 bg-theme-primary"></div>}
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 bg-theme-bg border border-theme-border rounded flex items-center justify-center shrink-0">
                            <svg className="w-4 h-4 text-theme-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                            </svg>
                          </div>
                          <div className="min-w-0">
                            <button
                               className="font-semibold text-theme-text hover:underline underline-offset-2 truncate max-w-[240px] text-left block"
                               onClick={() => {
                                 const f = files.find(fileItem => fileItem.id === entry.fileId);
                                 if (f) {
                                   setSelectedFile(f);
                                   setDrawerNotes(f.notes || "");
                                   setFileHistory([entry]);
                                   setHistoryLoading(false);
                                   loadLogs(entry.id);
                                 }
                               }}
                            >
                              {entry.name}
                            </button>
                            <div className="text-[10px] font-mono text-theme-text-muted mt-0.5">{entry.id}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4 text-sm text-theme-text-muted whitespace-nowrap">{entry.operator || "-"}</td>
                      <td
                        className="px-4 py-4 text-sm text-theme-text-muted whitespace-nowrap"
                        title={entry.timestamp ? `${new Date(entry.timestamp).toLocaleString("en-US", { timeZone: "America/Los_Angeles" })} PST` : undefined}
                      >
                        {formatDateTime(entry.timestamp)}
                      </td>
                      <td className="px-4 py-4">
                        <StatusPill status={entry.status} size="sm" />
                      </td>
                      <td className="px-6 py-4">
                        {entry.fileId ? (
                            <div className="flex items-center gap-2 justify-end">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const f = files.find(fileItem => fileItem.id === entry.fileId);
                                  if (f) handleDownload(f);
                                }}
                                className="p-1.5 hover:text-theme-text hover:bg-theme-bg/50 rounded transition-colors text-theme-text-muted"
                                title="Download HEX file"
                              >
                                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                                </svg>
                              </button>
                              <Button
                                variant="secondary"
                                className="px-3 py-1 text-xs font-semibold"
                                onClick={() => {
                                  const f = files.find(fileItem => fileItem.id === entry.fileId);
                                  if (f) handleLoad(f);
                                }}
                                disabled={loadingId !== null}
                              >
                                Load
                              </Button>
                            </div>
                        ) : null}
                      </td>
                    </tr>
                    );
                  }) : sortedFiles.map((file, i) => {
                    const isLatest = file.id === latestFlashedFileId;
                    return (
                    <tr
                      key={file.id}
                      className={`border-b border-theme-border last:border-b-0 hover:bg-theme-bg/50 transition-colors ${
                        isLatest 
                          ? "bg-theme-primary/10 relative" 
                          : i % 2 === 0 ? "" : "bg-theme-bg/20"
                      }`}
                    >
                      <td className="px-6 py-4 relative">
                        {isLatest && <div className="absolute left-0 top-0 bottom-0 w-1 bg-theme-primary"></div>}
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
                    );
                  })}
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
                <label htmlFor="drawer-notes" className="text-xs font-bold text-theme-text-muted tracking-wider">FILE NOTES</label>
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
                      <div className="mt-1">
                        <FlashHistoryNoteEditor entryId={entry.id} initialNotes={entry.notes || ""} />
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

function FlashHistoryNoteEditor({ entryId, initialNotes }: { entryId: string, initialNotes: string }) {
  const [val, setVal] = useState(initialNotes);
  
  useEffect(() => setVal(initialNotes), [initialNotes]);

  const handleBlur = async () => {
    if (val.trim() === initialNotes.trim()) return;
    try {
      await updateFlashHistoryNotes(entryId, val);
    } catch {
      setVal(initialNotes);
    }
  };

  return (
    <div className="relative group">
      <textarea
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onBlur={handleBlur}
        rows={val ? undefined : 1}
        placeholder="Add specific notes for this flash record..."
        className="w-full bg-theme-panel border border-theme-border text-theme-text px-3 py-2 text-xs rounded focus:outline-none focus:ring-1 focus:ring-theme-primary shadow-sm placeholder:text-theme-text-muted placeholder:opacity-50 resize-none transition-colors h-9"
      />
    </div>
  );
}
