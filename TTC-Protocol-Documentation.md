# TTC-Downloader Protocol Documentation (Code-Validated)

Validated primarily against:
- `TTC-Downloader/BWSA2E4wXNV3pvNwisH/IeDF7Q44WaQg86SboLp.cs`
- `TTC-Downloader/pQU8Yr4df7OBtXp84hX/OvGkrw4csBeumSmarYf.cs`

This document reflects implemented behavior in this codebase.

---

## 1. Transport Defaults

### CAN

| Parameter | Value |
|-----------|-------|
| TX CAN ID (Downloader -> ECU) | `0x01` (11-bit standard) |
| RX CAN ID (ECU -> Downloader) | `0x02` (11-bit standard) |
| Baudrate range | 10K to 1M |
| Default baudrate | 500K |
| Max CAN payload | 8 bytes |

### Ethernet/UDP

| Parameter | Value |
|-----------|-------|
| Default multicast IP | `239.0.0.1` |
| Default UDP port | `8500` |
| Max UDP payload used by tool | 1472 bytes |

---

## 2. Frame Format

### Common request prefix (CAN and UDP)

```
Byte 0: Command
Byte 1: Node ID (0xFF is broadcast)
Byte 2+: Payload (command specific)
```

### CAN specifics

- CAN frame length is variable (`2..8`), not always fixed to 8.
- Command + node consume 2 bytes, leaving up to 6 payload bytes per CAN frame.

### UDP specifics

- Same command/node prefix, but variable payload length.
- Some commands have different payload schemas in UDP vs CAN to exploit larger payload size.

---

## 3. Command Map (0x01-0x20)

| Cmd    | Name (in code behavior)                   | Notes                                                                                          |
| ------ | ----------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `0x01` | Identify request (ping/discovery trigger) | Typically sent to node `0xFF` for discovery.                                                   |
| `0x02` | LifeSign/status counters                  | Returns two 16-bit counters used to validate write progress.                                   |
| `0x03` | SelectNode (legacy/simple)                | Basic node select command (no strict ACK validation in current call path).                     |
| `0x04` | MemRead                                   | CAN: `Addr(4)+Len(1)`. UDP: `Addr(4)+Len(2)`.                                                  |
| `0x05` | MemWrite                                  | CAN writes data chunks (up to 6 bytes) after `0x0D` pointer setup. UDP embeds `Addr+Len+Data`. |
| `0x06` | EepromRead                                | CAN: `Addr(4)+Len(1)`. UDP: `Addr(4)+Len(2)`.                                                  |
| `0x07` | EepromWrite                               | CAN writes chunks after `0x0A` pointer setup. UDP embeds `Addr+Len+Data`.                      |
| `0x08` | EepromCRC                                 | Returns CRC32.                                                                                 |
| `0x09` | Legacy long-timeout command               | 120s timeout path; in this build it is wired through the EEPROM-erase flow.                    |
| `0x0A` | EepromSetAddress                          | Sets EEPROM pointer/address.                                                                   |
| `0x0B` | FlashWrite                                | Not CRC-based payload. Uses destination + size and RAM source.                                 |
| `0x0C` | FlashErase                                | Erases flash regions.                                                                          |
| `0x0D` | MemSetAddress                             | CAN pointer setup for RAM operations. UDP path logs unsupported.                               |
| `0x0E` | Execute                                   | Jump to address.                                                                               |
| `0x0F` | Call                                      | Call address.                                                                                  |
| `0x10` | MemCRC                                    | Returns CRC32 over memory region.                                                              |
| `0x11` | SelectNodeEx                              | `RandomID(4)+Flag(1)`, ACK echoes random ID.                                                   |
| `0x12` | SetComIdUp                                | Set communication ID Up (2-byte or 4-byte variant).                                            |
| `0x13` | SetComIdDown                              | Set communication ID Down (2-byte or 4-byte variant).                                          |
| `0x14` | Identify response                         | Discovery response format differs between CAN and UDP.                                         |
| `0x15` | GetHWType                                 | Returns `HWType(4)+Version(2)`.                                                                |
| `0x16` | Protect BL sectors (encrypted param)      | Active command in code.                                                                        |
| `0x17` | Encrypted APDB query                      | Active command in code.                                                                        |
| `0x18` | SetCRCInitValue                           | Active command, encrypted input before send.                                                   |
| `0x19` | Password command                          | Returns success byte.                                                                          |
| `0x1A` | Challenge/list command                    | Used in UDP identify flow; returns repeated entries.                                           |
| `0x1B` | RAMBufferWrite                            | Active command in safe download/auth flows.                                                    |
| `0x1C` | RAMBufferReset                            | Active command.                                                                                |
| `0x1D` | ExecuteAuthenticated                      | Active command.                                                                                |
| `0x1E` | RAMBufferFlash                            | Active command.                                                                                |
| `0x1F` | ApplicationCRC                            | Supports CRC32 and CRC64 paths.                                                                |
| `0x20` | SectorsComplete                           | ECC/post-processing trigger; includes address and count payload.                               |

---

## 4. Important Encoding Rules

### Endianness

- Addresses and 16/32-bit values are sent big-endian in command payloads unless explicitly transformed by the code's encrypted helpers.

### Size fields in flash commands

- `0x0B` (FlashWrite), `0x0C` (FlashErase), and `0x20` (SectorsComplete count field) encode size/count as `value - 1` before transmit.
- Example: erase 64KB means encoded size field `0xFFFF`.

### `0x0B` FlashWrite payload (critical correction)

- CAN path:
  - `0x0D MemSetAddress` sets RAM source pointer.
  - `0x0B` sends `DestAddr(4)+SizeMinus1(2)`.
- UDP path:
  - `0x0B` sends `DestAddr(4)+SizeMinus1(2)+SrcAddr(4)`.
- ACK is `[0x0B][Node][SuccessByte]`.

---

## 5. Discovery / Selection Behavior

### CAN discovery

- Request: command `0x01` (usually node `0xFF`).
- Response (`0x14`) expected length 6:

```
[14][Node][RandomID3][RandomID2][RandomID1][RandomID0]
```

### UDP discovery

- Request: identify polling uses command `0x14` in the main UDP identify path.
- Response (`0x14`) expected length 8:
  - Random ID at bytes `2..5`
  - Node ID at byte `7`

### Secure selection

- `0x11 SelectNodeEx` is used with random ID and flag.
- ACK length is 6 and must echo the same random ID.

---

## 6. Flashing Flow Used by the App

High-level flow used in updater code:

1. Identify and select node (`0x01`/`0x14`, then `0x11`).
2. Optional password/auth path (`0x19`) depending target features.
3. Erase flash sectors (`0x0C`) in contiguous blocks up to 64KB encoded size.
4. Upload data chunks into RAM buffer (`0x05`/`0x1B` depending path).
5. Program flash from RAM buffer (`0x0B`) in up to 64KB write operations.
6. Verify CRC (`0x1F` when supported, otherwise `0x10` memory CRC fallback).
7. If supported, call `0x20` per sector (`SectorsComplete`) to trigger post-processing/ECC.
8. Execute/reset application path (`0x0E`, plus auth/select transitions as needed).

---

## 7. Corrected CAN Examples

### Identify broadcast

```
TX (ID 0x01): [01 FF]
RX (ID 0x02): [14 05 3A 7B 12 9C]
```

### SelectNodeEx

```
TX: [11 05 3A 7B 12 9C 01]
RX: [11 05 3A 7B 12 9C]
```

### Flash erase 64KB at 0x08000000

```
TX: [0C 05 08 00 00 00 FF FF]
RX: [0C 05 01]
```

### Flash write 256 bytes at 0x08000000 (CAN path)

```
# Set source RAM address first:
TX: [0D 05 20 00 00 00]
RX: [0D 05]

# Then write with SizeMinus1 = 0x00FF:
TX: [0B 05 08 00 00 00 00 FF]
RX: [0B 05 01]
```

---

## 8. Notes

1. This protocol is not a simple fixed 8-byte mirror protocol; CAN and UDP variants diverge for several commands.
2. Earlier documentation that mapped `0x02` to hardware info and `0x0B` to CRC payload is incorrect for this codebase.
3. `0x15` is the hardware info command (`HW type + bootloader version`), not `0x02`.
4. `0x0A` is EEPROM set-address, not reset.

---
## 9. Deployment Sequence (Flashing to ECU)

This section describes the sequence the downloader follows when deploying firmware.

### 9.1 CAN sequence (typical application deploy)

1. Discover node(s):
- Send `0x01` to node `0xFF`.
- Collect `0x14` responses and store `NodeID` + `RandomID`.

2. Select node/session:
- Send `0x11` with selected `NodeID`, its `RandomID`, and select flag.
- Validate ACK echoes same random ID.

3. Optional second-stage bootloader transition (target-dependent):
- Upload second-stage image to RAM using `0x05` (plus lifesign checks via `0x02`).
- Jump to second-stage entry point with `0x0E`.
- Wait about 250 ms, re-identify (`0x01`/`0x14`), then select again (`0x11`).

4. Compute erase plan from image memory ranges:
- Build flash sector list.
- Merge contiguous sectors into blocks up to 64 KB.

5. Erase flash blocks:
- Send `0x0C` for each block with `SizeMinus1` encoding.
- Expect ACK `[0x0C][Node][0x01]`.

6. Program flash data:
- Split image regions into chunks that fit second-stage RAM buffer.
- Upload each chunk to RAM buffer address using `0x05`.
- Trigger flash programming with `0x0B` in up to 64 KB operations.

7. Verify:
- Preferred: `0x1F` application CRC (CRC32 or CRC64 depending feature).
- Fallback: `0x10` memory CRC over expected ranges.

8. Post-process (if feature enabled):
- Send `0x20` per sector (`SectorsComplete`) to trigger ECC/post-processing.
- Field values still use `value-1` encoding; sending one sector uses encoded value `0`.

9. Exit/app transition:
- Session/app transitions are handled through `0x11` select flows and optional `0x19` password path.
- In this build there is no clear dedicated "reset to app" command in normal flash flow.

### 9.2 UDP differences during deploy

- Identify polling in main UDP identify path uses command `0x14`.
- `0x04`/`0x06` use 2-byte length fields (instead of 1-byte CAN lengths).
- `0x05`/`0x07` carry `Addr+Len+Data` in one packet.
- `0x0B` carries source RAM address inline in UDP.
- Max payload used by downloader is 1472 bytes.

---

## 10. Confidence Levels (Confirmed vs Inferred)

### Confirmed by direct flash/deploy call paths

- `0x01`, `0x02`, `0x04`, `0x05`, `0x0B`, `0x0C`, `0x0E`, `0x10`, `0x11`, `0x14`, `0x1F`, `0x20`

### Confirmed command handlers, but feature/target dependent in deploy path

- `0x06`, `0x07`, `0x08`, `0x0A`, `0x12`, `0x13`, `0x15`, `0x19`

### Active in code but semantics partly inferred from naming/usage

- `0x09`, `0x16`, `0x17`, `0x18`, `0x1A`, `0x1B`, `0x1C`, `0x1D`, `0x1E`

---

## 11. Raspberry Pi Implementation Guidance

For a reliable Pi implementation, do not blindly replay captured traffic. Implement the protocol state machine:

1. Track dynamic session fields:
- Node ID, Random ID, and challenge/password-related values are dynamic.

2. Validate every ACK:
- Match command, node, and expected ACK length/content.
- Treat unexpected frames as noise and keep waiting until timeout.

3. Respect timeout classes:
- Normal command timeout is around 3 s.
- Flash erase up to ~40 s, flash write up to ~90 s, command `0x09` path up to ~120 s.

4. Implement exact encodings:
- Big-endian fields.
- `SizeMinus1` for `0x0B`, `0x0C`, and `0x20` count payload.

5. Bench-test only first:
- Validate on spare ECU/hardware-in-loop before any in-vehicle test.
- A wrong erase/write sequence can permanently brick an ECU.

---
