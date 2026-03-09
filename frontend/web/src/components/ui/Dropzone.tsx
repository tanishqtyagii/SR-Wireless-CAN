import React, { useCallback, useRef } from "react";

const STORED_HEX_DRAG_TYPE = "application/x-sr-hex-id";

interface DropzoneProps {
  onFileSelect: (file: File) => void;
  onStoredFileDrop?: (fileId: string) => Promise<void> | void;
  accept?: string;
  className?: string;
  isDragging?: boolean;
}

export function Dropzone({ onFileSelect, onStoredFileDrop, accept = ".hex", className = "" }: DropzoneProps) {
  const [dragActive, setDragActive] = React.useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const storedFileId = e.dataTransfer.getData(STORED_HEX_DRAG_TYPE);
    if (storedFileId && onStoredFileDrop) {
      await onStoredFileDrop(storedFileId);
      return;
    }

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      onFileSelect(e.dataTransfer.files[0]);
    }
  }, [onFileSelect, onStoredFileDrop]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      onFileSelect(e.target.files[0]);
    }
  }, [onFileSelect]);

  const onButtonClick = () => {
    inputRef.current?.click();
  };

  return (
    <div
      className={`relative w-full border-2 border-dashed rounded-lg p-8 text-center transition-colors focus-within:ring-2 focus-within:ring-black dark:focus-within:ring-white focus-within:border-transparent flex items-center justify-center ${
        dragActive ? "border-black dark:border-white bg-gray-50 dark:bg-zinc-800/50" : "border-gray-300 dark:border-zinc-700 hover:border-gray-400 dark:hover:border-zinc-500 bg-white dark:bg-zinc-900"
      } ${className}`}
      onDragEnter={handleDrag}
      onDragLeave={handleDrag}
      onDragOver={handleDrag}
      onDrop={handleDrop}
      role="button"
      tabIndex={0}
      onClick={onButtonClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onButtonClick();
        }
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        className="hidden"
        aria-label="Upload HEX file"
      />
      <div className="flex flex-col items-center justify-center gap-3 pointer-events-none">
        <svg className="w-10 h-10 text-gray-400 dark:text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V6.75m0 0L8.25 10.5M12 6.75l3.75 3.75" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 15.75v1.5A2.25 2.25 0 006 19.5h12a2.25 2.25 0 002.25-2.25v-1.5" />
        </svg>
        <div>
          {dragActive ? (
            <p className="text-sm font-medium text-gray-900 dark:text-zinc-200">
              Drop to load
            </p>
          ) : (
            <>
              <p className="text-sm font-medium text-gray-900 dark:text-zinc-200">
                Click to upload or drag and drop
              </p>
              <p className="text-xs text-gray-500 dark:text-zinc-400 mt-1">
                HEX files only
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
