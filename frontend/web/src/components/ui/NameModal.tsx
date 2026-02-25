import { useState, useRef, useEffect } from "react";
import { Button } from "./Button";

interface NameModalProps {
  onConfirm: (name: string) => void;
}

export function NameModal({ onConfirm }: NameModalProps) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onConfirm(trimmed);
  };

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-theme-bg">
      <div className="w-full max-w-sm mx-4 bg-theme-panel border border-theme-border rounded-xl shadow-2xl p-8 flex flex-col gap-6">
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-bold text-theme-text">Who are you?</h1>
          <p className="text-sm text-theme-text-muted">Your name will be recorded with every flash.</p>
        </div>

        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="e.g. Alex"
          maxLength={64}
          className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-3 rounded focus:outline-none focus:ring-2 focus:ring-theme-primary text-sm"
        />

        <Button
          variant="primary"
          className="w-full py-3 font-semibold"
          onClick={submit}
          disabled={!value.trim()}
        >
          Continue
        </Button>
      </div>
    </div>
  );
}
