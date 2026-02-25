import { DragEvent, useEffect, useState } from "react";
import { useVcuApp } from "../hooks/useVcuApp";
import { clearAllData } from "../services/api";
import { Button } from "../components/ui/Button";
import { Dropzone } from "../components/ui/Dropzone";
import { StatusPill } from "../components/ui/StatusPill";
import { DetailDrawer } from "../components/ui/DetailDrawer";
import { NameModal } from "../components/ui/NameModal";
import { FlashHistoryEntry } from "../types";
import { Panel, PanelHeader, PanelTitle, PanelContent } from "../components/ui/Panel";
import { InlineAlert } from "../components/ui/InlineAlert";

const STORED_HEX_DRAG_TYPE = "application/x-sr-hex-id";
const OPERATOR_KEY = "sr_operator_name";

export default function FlashPage() {
  const { 
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
  } = useVcuApp();
  
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [notes, setNotes] = useState("");
  const [operatorName, setOperatorName] = useState<string>(
    () => localStorage.getItem(OPERATOR_KEY) ?? ""
  );
  const [drawerItem, setDrawerItem] = useState<FlashHistoryEntry | null>(null);
  const [editingNotes, setEditingNotes] = useState("");
  const [isSavingNotes, setIsSavingNotes] = useState(false);
  const [notesError, setNotesError] = useState("");
  const [dragError, setDragError] = useState("");
  const [isDragReplace, setIsDragReplace] = useState(false);
  const [replaceNotice, setReplaceNotice] = useState("");

  const clearFile = () => {
    setSelectedFile(null);
    setDisplayName("");
    setNotes("");
    setReplaceNotice("");
  };

  const replaceFile = (file: File) => {
    setSelectedFile(file);
    setDisplayName(file.name);
    setNotes("");
    setReplaceNotice(`Replaced with "${file.name}"`);
    setTimeout(() => setReplaceNotice(""), 3000);
  };

  const isBootloading = vcuState === "bootloading";
  const vcuIsBusy = vcuState === "flashing";
  const canFlash = selectedFile && !isBusy && !vcuIsBusy;

  const onAction = async (actionFn: (fd: FormData) => Promise<void>) => {
    if (!selectedFile) return;
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("displayName", displayName || selectedFile.name);
    formData.append("notes", notes);
    formData.append("operator", operatorName);
    await actionFn(formData);
    clearFile();
  };

  useEffect(() => {
    setEditingNotes(drawerItem?.notes ?? "");
    setNotesError("");
  }, [drawerItem]);

  const handleSaveNotes = async () => {
    if (!drawerItem) return;

    setIsSavingNotes(true);
    setNotesError("");

    try {
      await handleUpdateHistoryNotes(drawerItem.id, editingNotes);
      setDrawerItem({
        ...drawerItem,
        notes: editingNotes.trim() ? editingNotes.trim() : undefined,
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Failed to save notes.";
      setNotesError(message);
    } finally {
      setIsSavingNotes(false);
    }
  };

  const notesChanged = (editingNotes.trim() !== (drawerItem?.notes ?? "").trim());

  const onStoredFileDrop = async (fileId: string) => {
    try {
      const file = await handleSelectStoredHex(fileId);
      if (selectedFile) {
        replaceFile(file);
      } else {
        setSelectedFile(file);
        setDisplayName(file.name);
        setNotes("");
      }
      setDragError("");
    } catch {
      setDragError("This flash record has no stored HEX payload to drag.");
    }
  };

  const handlePanelDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDragReplace(true);
  };

  const handlePanelDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node)) {
      setIsDragReplace(false);
    }
  };

  const handlePanelDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragReplace(false);

    const storedId = event.dataTransfer.getData(STORED_HEX_DRAG_TYPE);
    if (storedId) {
      await onStoredFileDrop(storedId);
      return;
    }

    const file = event.dataTransfer.files?.[0];
    if (file) {
      replaceFile(file);
    }
  };

  const startHistoryDrag = (event: DragEvent<HTMLLIElement>, entry: FlashHistoryEntry) => {
    const fileId = entry.fileId ?? "";
    event.dataTransfer.setData(STORED_HEX_DRAG_TYPE, fileId);
    event.dataTransfer.effectAllowed = "copy";
  };

  return (
    <div className="min-h-screen bg-theme-bg text-theme-text font-sans flex flex-col p-4 gap-4 h-screen overflow-hidden">
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
        <div className="flex items-center gap-3">
          <img src="/branding/spartan-logo.png" alt="Spartan Racing" className="h-10 w-auto object-contain" />
        </div>
        <div className="flex items-center gap-3">
          {operatorName && (
            <button
              onClick={() => {
                localStorage.removeItem(OPERATOR_KEY);
                setOperatorName("");
              }}
              className="text-xs text-theme-text-muted hover:text-theme-text border border-theme-border px-2.5 py-1.5 rounded-full transition-colors"
              title="Switch operator"
            >
              {operatorName}
            </button>
          )}
          <div className="flex items-center gap-3 border border-theme-border bg-theme-panel px-3 py-1.5 rounded-full shadow-sm">
            <span className="text-xs font-semibold text-theme-text-muted tracking-wide">CURRENT STATE</span>
            <StatusPill status={vcuState} className="!border-none !bg-transparent !p-0 !text-xs" />
          </div>
          <button
            onClick={async () => {
              if (!window.confirm("Clear all stored files and flash history? This cannot be undone.")) return;
              await clearAllData();
              clearFile();
              window.location.reload();
            }}
            className="text-xs text-theme-text-muted hover:text-red-400 transition-colors border border-theme-border px-2.5 py-1.5 rounded-full"
            title="Clear all local data"
          >
            Clear DB
          </button>
        </div>
      </header>

      {/* Body Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0 w-full">
        <div className="lg:col-span-9 flex flex-col min-h-0 gap-4">
          <Panel className="flex-1 min-h-0 bg-theme-panel border border-theme-border shadow-sm">
            <div className="h-full relative overflow-y-auto p-6">
              {!selectedFile ? (
                <div className="h-full flex items-center justify-center">
                  <Dropzone 
                    onFileSelect={(f) => { 
                      setSelectedFile(f); 
                      setDisplayName(f.name); 
                      setNotes(""); 
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
                    onChange={(e) => setDisplayName(e.target.value)} 
                    className="w-full bg-transparent text-xl font-bold text-theme-text text-center focus:outline-none focus:ring-2 focus:ring-theme-primary rounded px-2 py-1 border-b border-transparent hover:border-theme-border transition-colors mb-2" 
                    placeholder="Filename" 
                  />
                  <div className="text-sm text-theme-text-muted mb-8">
                    {(selectedFile.size / 1024).toFixed(2)} KB • {new Date(selectedFile.lastModified).toLocaleDateString()}
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
                      onChange={(e) => setNotes(e.target.value)}
                      className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-3 rounded focus:outline-none focus:ring-2 focus:ring-theme-primary min-h-[120px] resize-none text-sm"
                      placeholder="Add build notes or version info..."
                    />
                  </div>
                </div>
              )}
            </div>
          </Panel>

          <Panel className="shrink-0 bg-theme-panel border border-theme-border shadow-sm">
            <PanelHeader>
              <PanelTitle>Execution Sequence</PanelTitle>
            </PanelHeader>
            <PanelContent className="p-4">
              {errorMessage && (
                <div className="mb-4">
                  <InlineAlert variant="error" message={errorMessage} />
                </div>
              )}

              {vcuIsBusy && !isBusy && (
                <div className="mb-4">
                  <InlineAlert variant="warning" message="VCU is currently flashing from another session. All operations are locked until it returns to idle." />
                </div>
              )}

              <div className="flex items-stretch gap-4">
                {/* Two-step flow */}
                <div className="flex-1 grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <Button 
                      variant="primary" 
                      className="w-full py-3 font-semibold text-sm" 
                      onClick={handleBoot} 
                      disabled={isBusy || isBootloading || vcuIsBusy}
                      isLoading={isBusy && vcuState === "bootloading" && !selectedFile}
                    >
                      1. Enter Bootloader
                    </Button>
                  </div>

                  <div className="flex flex-col gap-1">
                    <Button 
                      variant="outline" 
                      className={`w-full py-3 font-semibold text-sm ${
                        isBootloading ? 'border-theme-primary text-theme-primary' : 'text-theme-text-muted'
                      }`} 
                      onClick={() => canFlash && onAction(handleFlashOnly)} 
                      disabled={!canFlash || !isBootloading || isBusy}
                      isLoading={isBusy && vcuState === "flashing"}
                    >
                      2. Flash Binary
                    </Button>
                  </div>
                </div>

                {/* OR divider */}
                <div className="flex flex-col items-center justify-center gap-1 shrink-0">
                  <div className="w-px flex-1 bg-theme-border" />
                  <span className="text-xs font-bold text-theme-text-muted px-1">OR</span>
                  <div className="w-px flex-1 bg-theme-border" />
                </div>

                {/* Single-step shortcut */}
                <div className="flex flex-col gap-1 w-48 shrink-0">
                  <Button 
                    variant="outline" 
                    className="w-full py-3 font-semibold text-sm border-theme-border" 
                    onClick={() => canFlash && onAction(handleBootAndFlash)} 
                    disabled={!canFlash || isBusy}
                    isLoading={isBusy && (vcuState === "bootloading" || vcuState === "flashing")}
                  >
                    Bootload + Flash
                  </Button>
                </div>
              </div>

              <div className="mt-4 pt-3 border-t border-theme-border text-center">
                <div className="text-xs text-theme-text-muted mb-1">Status</div>
                <div className="text-sm font-medium text-theme-text">
                  {vcuState === "flashing"
                    ? `Flashing...`
                    : vcuState === "bootloading"
                    ? "Ready to flash"
                    : isBusy
                    ? "Working..."
                    : "Waiting for sequence"}
                </div>
              </div>
            </PanelContent>
          </Panel>
        </div>

        {/* RIGHT COLUMN: History */}
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
                {history.map(entry => (
                  <li 
                    key={entry.id} 
                    draggable
                    onDragStart={(event) => startHistoryDrag(event, entry)}
                    className="p-3 hover:bg-theme-panel-hover cursor-grab active:cursor-grabbing flex justify-between items-start"
                    title="Drag into upload area"
                    role="button"
                    tabIndex={0}
                    onClick={() => setDrawerItem(entry)}
                    onKeyDown={(e) => e.key === 'Enter' && setDrawerItem(entry)}
                  >
                    <div className="min-w-0 pr-2">
                      <div className="text-sm font-semibold text-theme-text truncate">{entry.name}</div>
                      <div className="text-xs text-theme-text-muted mt-0.5">
                        {entry.operator && <span className="mr-1.5">{entry.operator} &middot;</span>}
                        {new Date(entry.timestamp).toLocaleTimeString()}
                      </div>
                    </div>
                    <StatusPill status={entry.status} size="sm" className="shrink-0" />
                  </li>
                ))}
                {history.length === 0 && (
                  <div className="p-6 text-center text-sm text-theme-text-muted">No flash history.</div>
                )}
              </ul>
            </PanelContent>
          </Panel>
        </div>
      </div>

      <DetailDrawer isOpen={!!drawerItem} onClose={() => setDrawerItem(null)} title="Flash Details">
        {drawerItem && (
          <div className="space-y-6">
            <div className="space-y-1">
              <h2 className="text-lg font-bold text-theme-text">{drawerItem.name}</h2>
              <div className="text-sm text-theme-text-muted">{new Date(drawerItem.timestamp).toLocaleString()}</div>
              {drawerItem.operator && (
                <div className="text-sm text-theme-text-muted">Flashed by <span className="font-semibold text-theme-text">{drawerItem.operator}</span></div>
              )}
            </div>
            
            <div className="bg-theme-bg rounded p-4 border border-theme-border">
              <div className="text-xs font-bold text-theme-text-muted mb-2">Status</div>
              <StatusPill status={drawerItem.status} />
            </div>

            <div className="space-y-2">
              <div className="text-xs font-bold text-theme-text-muted">Notes</div>
              <textarea
                value={editingNotes}
                onChange={(event) => setEditingNotes(event.target.value)}
                disabled={isSavingNotes}
                className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-3 rounded focus:outline-none focus:ring-2 focus:ring-theme-primary min-h-[140px] resize-none text-sm"
                placeholder="Add notes for this flash record..."
              />

              {notesError && (
                <InlineAlert variant="error" message={notesError} />
              )}

              <div className="flex justify-end pt-1">
                <Button
                  variant="primary"
                  className="text-xs"
                  onClick={handleSaveNotes}
                  disabled={isSavingNotes || !notesChanged}
                  isLoading={isSavingNotes}
                >
                  Save Notes
                </Button>
              </div>
            </div>

            {drawerItem.logs && drawerItem.logs.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-bold text-theme-text-muted">Flash Log</div>
                <div className="bg-theme-bg border border-theme-border rounded p-3 max-h-48 overflow-y-auto">
                  {drawerItem.logs.map((line, i) => (
                    <div key={`${i}-${line.slice(0, 32)}`} className="text-xs font-mono text-theme-text-muted leading-relaxed whitespace-pre-wrap">{line}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </DetailDrawer>
    </div>
  );
}
