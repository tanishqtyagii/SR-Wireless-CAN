import { useEffect, useRef } from "react";

interface LogModalProps {
  isOpen: boolean;
  onClose: () => void;
  logs: string[];
  vcuState: string;
}

export function LogModal({ isOpen, onClose, logs, vcuState }: LogModalProps) {
  const logScrollRef = useRef<HTMLDivElement>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom whenever logs change or modal opens (instant)
  useEffect(() => {
    if (isOpen && logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [logs, isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const isLive = vcuState === "flashing" || vcuState === "bootloading";

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-center justify-center bg-theme-overlay backdrop-blur-sm"
      onClick={onClose}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
    >
      <div
        role="dialog"
        aria-modal={true}
        aria-label="Operation Log"
        className="w-full max-w-3xl mx-4 rounded-xl bg-theme-panel border border-theme-border shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-theme-border">
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-semibold text-theme-text">Operation Log</span>
            {isLive && (
              <span className="flex items-center gap-1.5 text-[10px] font-semibold tracking-widest uppercase text-green-400">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                LIVE
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {logs.length > 0 && (
              <span className="text-xs text-theme-text-muted font-mono">{logs.length} lines</span>
            )}
            <button
              onClick={onClose}
              className="p-1.5 text-theme-text-muted hover:text-theme-text hover:bg-theme-panel-hover rounded-full transition-colors"
              aria-label="Close"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Log body */}
        <div ref={logScrollRef} className="h-96 overflow-y-auto p-4 font-mono text-xs leading-relaxed bg-theme-bg">
          {logs.length === 0 ? (
            <div className="h-full flex items-center justify-center">
              <span className="text-theme-text-muted text-xs">No logs yet.</span>
            </div>
          ) : logs.map((line, index) => {
            const match = line.match(/^\[([^\]]+)\]\s*(.*)$/);
            const rawTs = match ? match[1] : null;
            const msg = match ? match[2] : line;
            let ts: string | null = null;
            if (rawTs) {
              const d = new Date(rawTs);
              ts = isNaN(d.getTime()) ? rawTs : d.toLocaleTimeString("en-US", {
                timeZone: "America/Los_Angeles",
                hour: "numeric",
                minute: "2-digit",
                second: "2-digit",
                hour12: true,
              }) + " PST";
            }
            const isPowerCycle = /power.?cycle/i.test(line);
            const isError = /error|fail/i.test(msg);
            const isSeparator = line.startsWith("────");
            const isSuccess = /success|complete|done|acknowledged/i.test(msg);
            const isInfo = /starting|uploading|entering|running|vcu/i.test(msg);

            if (isSeparator) {
              return (
                <div key={`sep-${index}`} className="text-theme-border my-2 select-none">
                  {line}
                </div>
              );
            }
            return (
              <div key={`log-${index}`} className="flex gap-2 py-[1px]">
                {ts && <span className="shrink-0 text-theme-text-muted opacity-40">{ts}</span>}
                <span className={
                  isPowerCycle ? "text-red-400 font-bold" :
                  isError      ? "text-red-400" :
                  isSuccess    ? "text-green-400" :
                  isInfo       ? "text-sky-400" :
                  "text-theme-text"
                }>{msg || line}</span>
              </div>
            );
          })}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
}
