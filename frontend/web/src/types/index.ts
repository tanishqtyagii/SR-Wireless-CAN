export type VcuState = "idle" | "bootloading" | "flashing" | "error";
export type FlashStatus = "success" | "failed" | "unknown" | "pending";

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

export interface SystemEvent {
  type: "vcu_state" | "socket_error" | "flash_history_updated";
  state?: VcuState;
  error?: string;
}
