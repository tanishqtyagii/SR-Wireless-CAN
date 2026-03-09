import React, { useEffect } from "react";

interface DetailDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

export function DetailDrawer({ isOpen, onClose, title, children }: DetailDrawerProps) {
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Prevent background scrolling when open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "unset";
    }
    return () => {
      document.body.style.overflow = "unset";
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <>
      <div 
        className="fixed inset-0 bg-theme-overlay z-[90] transition-opacity backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      
      <div 
        className="fixed right-0 top-0 bottom-0 w-full sm:w-96 bg-theme-panel text-theme-text z-[100] shadow-2xl flex flex-col transform transition-transform duration-300 ease-out sm:border-l border-theme-border"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
      >
        <div className="flex items-center justify-between p-4 border-b border-theme-border bg-theme-panel">
          <h2 id="drawer-title" className="text-lg font-bold text-theme-text truncate pr-4">
            {title}
          </h2>
          <button 
            onClick={onClose}
            className="p-2 text-theme-text-muted hover:text-theme-text hover:bg-theme-panel-hover rounded-full transition-colors"
            aria-label="Close panel"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto p-6 text-theme-text">
          {children}
        </div>
      </div>
    </>
  );
}
