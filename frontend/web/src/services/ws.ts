/// <reference types="vite/client" />
import { VcuState } from "../types";

const BROADCAST_CHANNEL = "sr_wireless_can_state";

/**
 * Subscribe to vcuState changes broadcast by other tabs/windows.
 * Returns an unsubscribe function.
 */
export const subscribeToBroadcast = (onStateChange: (state: VcuState) => void): (() => void) => {
  try {
    const ch = new BroadcastChannel(BROADCAST_CHANNEL);
    ch.onmessage = (event) => {
      if (event.data?.type === "vcu_state" && typeof event.data.state === "string") {
        onStateChange(event.data.state as VcuState);
      }
    };
    return () => ch.close();
  } catch {
    return () => {};
  }
};
