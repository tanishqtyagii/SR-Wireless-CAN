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
      <div className="w-full max-w-sm mx-4 flex flex-col gap-8">
        {/* Logo */}
        <div className="flex flex-col items-center gap-3">
          <img src="/branding/spartan-logo.png" alt="Spartan Racing" className="h-14 w-auto object-contain opacity-90" />
          <div className="text-xs font-semibold tracking-widest text-theme-text-muted uppercase">VCU Flash Tool</div>
        </div>

        {/* Card */}
        <div className="bg-theme-panel border border-theme-border rounded-2xl shadow-2xl p-8 flex flex-col gap-5">
          <div className="flex flex-col gap-1">
            <h1 className="text-lg font-bold text-theme-text">Sign in</h1>
            <p className="text-sm text-theme-text-muted">Enter your name to be recorded with each flash.</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="operator-name" className="text-xs font-semibold text-theme-text-muted tracking-wide uppercase">Your name</label>
            <input
              id="operator-name"
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="e.g. Akash"
              maxLength={64}
              className="w-full bg-theme-bg border border-theme-border text-theme-text px-4 py-2.5 rounded-lg focus:outline-none focus:ring-2 focus:ring-theme-primary text-sm placeholder:text-theme-text-muted/50"
            />
          </div>

          <Button
            variant="primary"
            className="w-full py-2.5 font-semibold"
            onClick={submit}
            disabled={!value.trim()}
          >
            Continue
          </Button>
        </div>
      </div>
    </div>
  );
}
