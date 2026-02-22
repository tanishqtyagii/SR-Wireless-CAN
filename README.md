# SR Wireless CAN Bootloader

## Constants

| Name         | Value                  |
|--------------|------------------------|
| Session Token | `01 81 16 92 AE`      |
| Host → VCU   | CAN ID `0x001`         |
| VCU → Host   | CAN ID `0x002`         |

---

## Flashing Protocol Sequence

### 1. Init
Three frames sent to prime the VCU before the bootload trigger.

| CAN ID | Data                     | Delay  |
|--------|--------------------------|--------|
| 0x001  | `11 FF 00 00 00 00 01`   | 0.5 ms |
| 0x001  | `03 FF`                  | 0.7 ms |
| 0x001  | `01 FF`                  | 0.9 ms |

---

### 2. Bootload Trigger ⚠️ Power Cycle Required
Host blasts the bootload command across CAN IDs `0x600`–`0x6FF`.
**The VCU must be power cycled within this window.**

| CAN ID      | Data                       | Delay  |
|-------------|----------------------------|--------|
| 0x600–0x6FF | `2B 25 10 01 13 03 00 00`  | 10 ms  |
| 0x001       | `01 FF`                    | 10 ms  |

---

### 3. Wait for VCU Boot
Host floods the bus with wait/buffer frames (~3.9 s total) while the VCU reboots.

| CAN ID | Data    | Count | Delay |
|--------|---------|-------|-------|
| 0x001  | `01 FF` | 650   | 6 ms  |

---

### 4. Boot Confirmation Handshake
Host polls with an incrementing byte until VCU echoes back with the session token.

| Direction | CAN ID | Data                       | Notes                        |
|-----------|--------|----------------------------|------------------------------|
| Host →    | 0x001  | `14 00` … `14 FF`          | Iterates 0x00–0xFF, 35 ms timeout each |
| ← VCU     | 0x002  | `14 01 81 16 92 AE`        | Loop breaks on this match    |

---

### 5. Heartbeat Check
| Direction | CAN ID | Data                     |
|-----------|--------|--------------------------|
| Host →    | 0x001  | `11 FF 00 00 00 00 00`   |
| Host →    | 0x001  | `11 01 81 16 92 AE 01`   |
| ← VCU     | 0x002  | `11 01 81 16 92 AE`      |

---

### 6. 0x17 Challenge
Purpose unclear — likely an auth/security exchange.

| Direction | CAN ID | Data                    |
|-----------|--------|-------------------------|
| Host →    | 0x001  | `17 01 B2 25 6A FC 00`  |
| ← VCU     | 0x002  | *(any response)*        |
| Host →    | 0x001  | `17 01 E9 30 5A 10 01`  |
| ← VCU     | 0x002  | *(any response)*        |

---

### 7. Second Heartbeat
Sent after a ~130 ms delay.

| Direction | CAN ID | Data                     |
|-----------|--------|--------------------------|
| Host →    | 0x001  | `11 FF 00 00 00 00 00`   |
| Host →    | 0x001  | `11 01 81 16 92 AE 01`   |
| ← VCU     | 0x002  | `11 01 81 16 92 AE`      |

---

### 8. Flash Confirmation
VCU signals it is ready to receive the firmware flash.

| Direction | CAN ID | Data                  | Meaning               |
|-----------|--------|-----------------------|-----------------------|
| Host →    | 0x001  | `0D 01 00 E0 00 00`   | Flash readiness check |
| ← VCU     | 0x002  | `0D 01`               | Ready for flash ✓     |

---

## Message Reference

### Host → VCU (`0x001`)

| Cmd  | Data                          | Description             |
|------|-------------------------------|-------------------------|
| 0x11 | `11 FF 00 00 00 00 01`        | Global session opener   |
| 0x11 | `11 FF 00 00 00 00 00`        | Session heartbeat       |
| 0x11 | `11 [Session Token] 01`       | Alive check             |
| 0x03 | `03 FF`                       | Init / prime            |
| 0x01 | `01 FF`                       | Wait / buffer           |
| 0x2B | `2B 25 10 01 13 03 00 00`     | Bootload trigger        |
| 0x14 | `14 [00–FF]`                  | Boot confirmation poll  |
| 0x17 | `17 01 B2 25 6A FC 00`        | Challenge frame 1       |
| 0x17 | `17 01 E9 30 5A 10 01`        | Challenge frame 2       |
| 0x0D | `0D 01 00 E0 00 00`           | Flash readiness check   |

### VCU → Host (`0x002`)

| Cmd  | Data                          | Description             |
|------|-------------------------------|-------------------------|
| 0x11 | `11 [Session Token]`          | Session alive echo      |
| 0x14 | `14 [Session Token]`          | Boot confirmation echo  |
| 0x0D | `0D 01`                       | Ready for flash         |
