import { useEffect, useState } from "react";
import { FlashHistoryEntry } from "../../types";
import { Button } from "../ui/Button";
import { DetailDrawer } from "../ui/DetailDrawer";
import { StatusPill } from "../ui/StatusPill";
import { InlineAlert } from "../ui/InlineAlert";

interface FlashDrawerProps {
  item: FlashHistoryEntry | null;
  onClose: () => void;
  onSaveNotes: (id: string, notes: string) => Promise<void>;
  onLoadFile?: (fileId: string) => void;
}

export function FlashDrawer({ item, onClose, onSaveNotes, onLoadFile }: FlashDrawerProps) {
  const [editingNotes, setEditingNotes] = useState(item?.notes ?? "");
  // Tracks the last successfully-saved value so the Save button disables after a save
  const [savedNotes, setSavedNotes] = useState(item?.notes ?? "");
  const [notesError, setNotesError] = useState("");

  // Reset local state only when a different item or saved note value is loaded
  useEffect(() => {
    setEditingNotes(item?.notes ?? "");
    setSavedNotes(item?.notes ?? "");
    setNotesError("");
  }, [item?.id, item?.notes]);

  const notesChanged = editingNotes.trim() !== savedNotes.trim();

  const handleBlur = async () => {
    if (!item || !notesChanged) return;
    setNotesError("");
    try {
      await onSaveNotes(item.id, editingNotes);
      setSavedNotes(editingNotes);
    } catch (err: unknown) {
      setNotesError(err instanceof Error ? err.message : "Failed to save notes.");
      setEditingNotes(savedNotes); // Revert on failure
    }
  };

  return (
    <DetailDrawer isOpen={!!item} onClose={onClose} title="Flash Details">
      {item && (
        <div className="space-y-6">
          <div className="space-y-1">
            <h2 className="text-lg font-bold text-theme-text">{item.name}</h2>
            <div className="text-sm text-theme-text-muted">
              {new Date(item.timestamp).toLocaleString()}
            </div>
            {item.operator && (
              <div className="text-sm text-theme-text-muted">
                Flashed by{" "}
                <span className="font-semibold text-theme-text">{item.operator}</span>
              </div>
            )}
            {item.fileId && (
              <div className="flex items-center flex-wrap gap-2 pt-2">
                {onLoadFile && (
                  <Button
                    variant="primary"
                    className="text-xs font-semibold px-3 py-1.5"
                    onClick={() => {
                      onLoadFile(item.fileId!);
                      onClose();
                    }}
                  >
                    Load into Flash
                  </Button>
                )}
                <a
                  href={`/api/hex-files/${item.fileId}/content`}
                  download={item.name}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 bg-theme-bg border border-theme-border hover:bg-theme-bg/50 hover:text-theme-text text-theme-text-muted rounded transition-colors shadow-sm"
                  title="Download HEX file"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                  </svg>
                  <span className="text-xs font-semibold">Download</span>
                </a>
              </div>
            )}
          </div>

          <div className="bg-theme-bg rounded p-4 border border-theme-border">
            <div className="text-xs font-bold text-theme-text-muted mb-2">Status</div>
            <StatusPill status={item.status} />
          </div>

          <div className="space-y-2 relative">
            <div className="text-xs font-bold text-theme-text-muted">
              Notes
            </div>
            <textarea
              value={editingNotes}
              onChange={(e) => setEditingNotes(e.target.value)}
              onBlur={handleBlur}
              className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-3 rounded focus:outline-none focus:ring-1 focus:ring-theme-primary min-h-[140px] resize-none text-sm shadow-sm"
              placeholder="Add notes for this flash record..."
            />
            {notesError && <InlineAlert variant="error" message={notesError} />}
          </div>

          {item.logs === undefined ? (
            <div className="space-y-2">
              <div className="text-xs font-bold text-theme-text-muted">Flash Log</div>
              <div className="bg-theme-bg border border-theme-border rounded p-3 text-xs text-theme-text-muted shadow-sm">
                Loading logs...
              </div>
            </div>
          ) : item.logs.length > 0 ? (
            <div className="space-y-2">
              <div className="text-xs font-bold text-theme-text-muted">Flash Log</div>
              <div className="bg-theme-bg border border-theme-border rounded p-3 max-h-48 overflow-y-auto shadow-sm">
                {item.logs.map((line, index) => (
                  <div
                    key={`${item.id}-${index}`}
                    className="text-xs font-mono text-theme-text-muted leading-relaxed whitespace-pre-wrap"
                  >
                    {line}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </DetailDrawer>
  );
}
