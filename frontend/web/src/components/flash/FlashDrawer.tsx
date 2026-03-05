import { useEffect, useState } from "react";
import { FlashHistoryEntry } from "../../types";
import { DetailDrawer } from "../ui/DetailDrawer";
import { StatusPill } from "../ui/StatusPill";
import { Button } from "../ui/Button";
import { InlineAlert } from "../ui/InlineAlert";

interface FlashDrawerProps {
  item: FlashHistoryEntry | null;
  onClose: () => void;
  onSaveNotes: (id: string, notes: string) => Promise<void>;
}

export function FlashDrawer({ item, onClose, onSaveNotes }: FlashDrawerProps) {
  const [editingNotes, setEditingNotes] = useState(item?.notes ?? "");
  // Tracks the last successfully-saved value so the Save button disables after a save
  const [savedNotes, setSavedNotes] = useState(item?.notes ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [notesError, setNotesError] = useState("");

  // Reset local state whenever the item changes
  useEffect(() => {
    setEditingNotes(item?.notes ?? "");
    setSavedNotes(item?.notes ?? "");
    setNotesError("");
  }, [item]);

  const notesChanged = editingNotes.trim() !== savedNotes.trim();

  const handleSave = async () => {
    if (!item) return;
    setIsSaving(true);
    setNotesError("");
    try {
      await onSaveNotes(item.id, editingNotes);
      setSavedNotes(editingNotes); // optimistic: disable Save button until next edit
    } catch (err: unknown) {
      setNotesError(err instanceof Error ? err.message : "Failed to save notes.");
    } finally {
      setIsSaving(false);
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
          </div>

          <div className="bg-theme-bg rounded p-4 border border-theme-border">
            <div className="text-xs font-bold text-theme-text-muted mb-2">Status</div>
            <StatusPill status={item.status} />
          </div>

          <div className="space-y-2">
            <div className="text-xs font-bold text-theme-text-muted">Notes</div>
            <textarea
              value={editingNotes}
              onChange={(e) => setEditingNotes(e.target.value)}
              disabled={isSaving}
              className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-3 rounded focus:outline-none focus:ring-2 focus:ring-theme-primary min-h-[140px] resize-none text-sm"
              placeholder="Add notes for this flash record..."
            />
            {notesError && <InlineAlert variant="error" message={notesError} />}
            <div className="flex justify-end pt-1">
              <Button
                variant="primary"
                className="text-xs"
                onClick={handleSave}
                disabled={isSaving || !notesChanged}
                isLoading={isSaving}
              >
                Save Notes
              </Button>
            </div>
          </div>

          {item.logs && item.logs.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-bold text-theme-text-muted">Flash Log</div>
              <div className="bg-theme-bg border border-theme-border rounded p-3 max-h-48 overflow-y-auto">
                {item.logs.map((line) => (
                  <div
                    key={line.slice(0, 40)}
                    className="text-xs font-mono text-theme-text-muted leading-relaxed whitespace-pre-wrap"
                  >
                    {line}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </DetailDrawer>
  );
}
