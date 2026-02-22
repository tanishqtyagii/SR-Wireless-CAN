import time
import can

# SocketCAN bitrate is configured at the OS level, e.g.:
# sudo ip link set can0 up type can bitrate 500000

frames = [
    (0x001, [0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x01]),  # DLC = 7
    (0x001, [0x03, 0xFF]),                                # DLC = 2
    (0x001, [0x01, 0xFF]),                                # DLC = 2
    (0x600, [0x2B, 0x25, 0x10, 0x01, 0x13, 0x03, 0x00, 0x00]),  # DLC = 8
]

with can.Bus(interface="socketcan", channel="can0") as bus:
    for arb_id, data in frames:
        msg = can.Message(
            arbitration_id=arb_id,
            data=data,                 # DLC inferred from len(data)
            is_extended_id=False       # PCAN "0001" strongly suggests 11-bit ID
        )
        bus.send(msg, timeout=0.2)
        print(f"Sent ID=0x{arb_id:X} DLC={len(data)} DATA={data}")
        time.sleep(0.01)  # optional pacing