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
  operator?: string;
  logs?: string[];
}
