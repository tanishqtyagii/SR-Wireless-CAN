export type VcuState = "idle" | "bootloading" | "bootloaded" | "flashing" | "error";
export type FlashStatus = "success" | "failed" | "unknown" | "pending";

export interface HexFile {
  id: string;
  name: string;
  displayName?: string;
  size: number;
  uploadedAt: string;
  lastFlashedAt?: string;
  lastFlashedBy?: string;
  status: "pending" | "success" | "failed";
  notes?: string;
}

export interface FlashHistoryEntry {
  id: string;
  fileId?: string;
  name: string;
  timestamp: string;
  status: FlashStatus;
  notes?: string;
  action?: "bootload" | "boot_and_flash" | "flash_only";
  operator?: string;
  logs?: string[];
}

export interface LibraryGroupedEntry {
  id: string;
  fileId?: string | null;
  name: string;
  displayName?: string;
  size?: number | null;
  uploadedAt?: string;
  lastFlashedAt?: string;
  lastFlashedBy?: string;
  status: FlashStatus | "pending";
  notes?: string;
  hasPayload: boolean;
}

export interface LibrarySnapshot {
  grouped: LibraryGroupedEntry[];
  flashes: FlashHistoryEntry[];
}
