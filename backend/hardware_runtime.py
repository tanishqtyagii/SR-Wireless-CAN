from __future__ import annotations

from typing import Any, Callable

ProgressCallback = Callable[[dict[str, Any]], None]


def _emit(on_event: ProgressCallback, **payload: Any) -> None:
    on_event({key: value for key, value in payload.items() if value is not None})


def _u32_be(x: int) -> list[int]:
    return [(x >> 24) & 0xFF, (x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF]


# ── Bootload ─────────────────────────────────────────────────────────────────

def bootload_with_progress(ctrl: Any, *, on_event: ProgressCallback, timeout_error: type[Exception]) -> dict[str, Any]:
    session_token = ctrl.session_token
    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response
    heartbeat = ctrl.heartbeat

    _emit(on_event, stage="bootload", phase="bootloading", progress=2, message="Sending wake-up frames")
    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x01], delay=0.5)
    send_can(canid=0x001, data=[0x03, 0xFF], delay=0.7)
    send_can(canid=0x001, data=[0x01, 0xFF], delay=0.9)

    _emit(
        on_event,
        stage="bootload",
        phase="bootloading",
        progress=18,
        powerCycle=True,
        message="Frame blast started. Power cycle required.",
    )
    for canid in range(0x600, 0x700):
        send_can(canid=canid, data=[0x2B, 0x25, 0x10, 0x01, 0x13, 0x03, 0x00, 0x00], delay=5)
        send_can(canid=0x001, data=[0x01, 0xFF], delay=3)

    _emit(
        on_event,
        stage="bootload",
        phase="bootloading",
        progress=34,
        powerCycle=False,
        message="Waiting for VCU to come back online after power cycle",
    )
    total_wait_frames = 650
    for idx in range(total_wait_frames):
        send_can(canid=0x001, data=[0x01, 0xFF], delay=6)
        if idx in {0, 149, 324, 499, 649}:
            progress = 34 + ((idx + 1) / total_wait_frames) * 22
            _emit(
                on_event,
                stage="bootload",
                phase="bootloading",
                progress=round(progress, 1),
                message=f"VCU reboot wait {idx + 1}/{total_wait_frames}",
            )

    _emit(on_event, stage="bootload", phase="bootloading", progress=60, message="Searching for 0x14 session response")
    matched = False
    for i in range(0x00, 0x100):
        send_can(canid=0x001, data=[0x14, i], delay=0.0)
        try:
            VCU_response(canid=0x002, data=[0x14, 0x01] + session_token, timeout=35)
            matched = True
            _emit(
                on_event,
                stage="bootload",
                phase="bootloading",
                progress=72,
                message=f"Session established after nonce 0x{i:02X}",
            )
            break
        except timeout_error:
            if i in {0x40, 0x80, 0xC0}:
                progress = 60 + (i / 0xFF) * 10
                _emit(
                    on_event,
                    stage="bootload",
                    phase="bootloading",
                    progress=round(progress, 1),
                    message=f"Still probing bootloader response ({i}/255)",
                )
            continue
    if not matched:
        raise RuntimeError("0x14 response failed")

    heartbeat()
    _emit(on_event, stage="bootload", phase="bootloading", progress=82, message="Heartbeat confirmed")

    send_can(canid=0x001, data=ctrl.key_0x17_1 + [0x00])
    VCU_response(canid=0x002, prefix=[0x17, 0x01], timeout=20)
    _emit(on_event, stage="bootload", phase="bootloading", progress=88, message="Applied challenge key 1/2")

    send_can(canid=0x001, data=ctrl.key_0x17_2 + [0x01])
    VCU_response(canid=0x002, prefix=[0x17, 0x01], timeout=20)
    _emit(on_event, stage="bootload", phase="bootloading", progress=95, message="Applied challenge key 2/2")

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00], delay=50)
    heartbeat()

    _emit(on_event, stage="bootload", phase="bootloading", progress=100, message="Bootloading successful")
    return {"status": "success"}


# ── Flash hex ────────────────────────────────────────────────────────────────

def flash_hex_with_progress(
    ctrl: Any,
    ih: Any,
    header80: list[int],
    *,
    on_event: ProgressCallback,
    flash_kernel_func: Callable[[Any], None],
    do_flash_kernel: bool = True,
    do_erase: bool = True,
) -> dict[str, Any]:
    flash_base = 0xC10000
    staging_addr = 0xE08000
    block_size = 0x8000
    chunk_size = 6
    poll_every_frames = 11

    send_delay_ms = 0
    ptr_timeout_ms = 1000
    poll_timeout_ms = 1000
    commit_timeout_ms = 3000
    erase_ack_timeout_ms = 2000

    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response
    hex_length = ctrl.hex_length

    if not isinstance(header80, list) or len(header80) != 0x80:
        raise ValueError("header80 must be a list of exactly 0x80 ints")
    if any((not isinstance(b, int) or b < 0 or b > 0xFF) for b in header80):
        raise ValueError("header80 elements must be ints 0..255")

    _emit(on_event, stage="flash_kernel", phase="flash_kernel", progress=0, message="Preparing flash kernel")
    if do_flash_kernel:
        flash_kernel_func(ctrl)
        _emit(on_event, stage="flash_kernel", phase="flash_kernel", progress=100, message="Flash kernel ready")
    else:
        send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xE0, 0x00, 0x00], delay=send_delay_ms)
        VCU_response(0x002, data=[0x0D, 0x01], timeout=ptr_timeout_ms)
        _emit(on_event, stage="flash_kernel", phase="flash_kernel", progress=100, message="Using existing flash kernel")

    length = hex_length(ih)

    if do_erase:
        erase_frames = calculate_0x0C_frames(ih, length)
        total_erase = max(1, len(erase_frames))
        for idx, frame in enumerate(erase_frames, start=1):
            send_can(0x001, frame, delay=send_delay_ms)
            VCU_response(0x002, prefix=[0x0C, 0x01, 0x01], timeout=erase_ack_timeout_ms)
            progress = (idx / total_erase) * 100.0
            _emit(
                on_event,
                stage="erase",
                phase="erasing",
                progress=round(progress, 1),
                message=f"Erasing flash region {idx}/{total_erase}",
                detail={"step": idx, "steps": total_erase},
            )
    else:
        _emit(on_event, stage="erase", phase="erasing", progress=100, message="Erase skipped")

    ih.padding = 0xFF
    image = bytearray(ih.tobinarray(start=flash_base, size=length))
    if len(image) < 0x80:
        raise ValueError("HEX image is smaller than 0x80 bytes; cannot apply APDB header")
    image[0:0x80] = bytes(header80)

    total_len = len(image)
    total_blocks = max(1, (total_len + block_size - 1) // block_size)
    mv = memoryview(image)

    send_can(0x001, [0x0D, 0x01] + _u32_be(staging_addr), delay=send_delay_ms)
    VCU_response(0x002, data=[0x0D, 0x01], timeout=ptr_timeout_ms)

    offset = 0
    block_index = 0
    while offset < total_len:
        this_len = min(block_size, total_len - offset)
        block = mv[offset: offset + this_len]

        frames_since_poll = 0
        bytes_since_poll = 0
        i = 0
        while i < this_len:
            chunk = block[i: i + chunk_size]
            send_can(0x001, [0x05, 0x01] + list(chunk), delay=send_delay_ms)
            frames_since_poll += 1
            bytes_since_poll += len(chunk)
            i += len(chunk)
            if frames_since_poll >= poll_every_frames:
                send_can(0x001, [0x02, 0x01], delay=send_delay_ms)
                expected = bytes_since_poll & 0xFF
                VCU_response(
                    0x002,
                    data=[0x02, 0x01, 0x00, expected, 0x00, 0x00],
                    timeout=poll_timeout_ms,
                )
                frames_since_poll = 0
                bytes_since_poll = 0

        if bytes_since_poll > 0:
            send_can(0x001, [0x02, 0x01], delay=send_delay_ms)
            expected = bytes_since_poll & 0xFF
            VCU_response(
                0x002,
                data=[0x02, 0x01, 0x00, expected, 0x00, 0x00],
                timeout=poll_timeout_ms,
            )

        dest_addr = flash_base + offset
        send_can(0x001, [0x0D, 0x01] + _u32_be(dest_addr), delay=send_delay_ms)
        VCU_response(0x002, data=[0x0D, 0x01], timeout=ptr_timeout_ms)

        len_m1 = this_len - 1
        commit = [0x0B, 0x01, 0x00, 0xE0, 0x80, 0x00, (len_m1 >> 8) & 0xFF, len_m1 & 0xFF]
        send_can(0x001, commit, delay=send_delay_ms)
        VCU_response(0x002, data=[0x0B, 0x01, 0x01], timeout=commit_timeout_ms)

        offset += this_len
        block_index += 1
        progress = (block_index / total_blocks) * 100.0
        _emit(
            on_event,
            stage="flash_hex",
            phase="flashing_hex",
            progress=round(progress, 1),
            message=f"Flashing block {block_index}/{total_blocks}",
            detail={
                "blockIndex": block_index,
                "totalBlocks": total_blocks,
                "bytesWritten": offset,
                "totalBytes": total_len,
            },
        )

        if offset < total_len:
            send_can(0x001, [0x0D, 0x01] + _u32_be(staging_addr), delay=send_delay_ms)
            VCU_response(0x002, data=[0x0D, 0x01], timeout=ptr_timeout_ms)

    return {
        "status": "success",
        "flash_base": hex(flash_base),
        "total_len": total_len,
        "blocks": block_index,
        "last_block_len": (total_len % block_size) or block_size,
        "erased": bool(do_erase),
        "kernel": bool(do_flash_kernel),
    }


def calculate_0x0C_frames(ih: Any, length: int) -> list[list[int]]:
    flash_base = 0xC10000
    session = 0x01
    chunk = 0x10000

    mn = ih.minaddr()
    mx = ih.maxaddr()
    if mn is None or mx is None:
        raise ValueError("HEX has no data records")
    if mx < flash_base:
        raise ValueError(f"HEX maxaddr 0x{mx:X} is below FLASH_BASE 0x{flash_base:X}")
    if length <= 0:
        return []

    frames: list[list[int]] = []
    addr = flash_base
    remaining = length
    while remaining > 0:
        this_len = chunk if remaining > chunk else remaining
        len_m1 = this_len - 1
        frames.append([
            0x0C,
            session,
            (addr >> 24) & 0xFF,
            (addr >> 16) & 0xFF,
            (addr >> 8) & 0xFF,
            addr & 0xFF,
            (len_m1 >> 8) & 0xFF,
            len_m1 & 0xFF,
        ])
        addr += this_len
        remaining -= this_len
    return frames


# ── Finalization ──────────────────────────────────────────────────────────────

def finalize_with_progress(ctrl: Any, *, on_event: ProgressCallback) -> dict[str, Any]:
    send_can = ctrl.send_can
    VCU_response = ctrl.VCU_response

    key_0x19_2 = ctrl.key_0x19_2
    key_0x19_1 = ctrl.key_0x19_1
    key_0x18 = [0xF5, 0x69, 0x5A, 0x48]
    key_0x18_2 = [0xB6, 0xE0, 0xC2, 0xEC]

    _emit(on_event, stage="finalize", phase="finalizing", progress=5, message="Starting finalization handshake")

    send_can(canid=0x001, data=[0x18, 0x01] + key_0x18)
    VCU_response(canid=0x002, prefix=[0x18, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, prefix=[0x0D, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x04, 0x01, 0x00, 0x00], timeout=10000)

    _emit(on_event, stage="finalize", phase="finalizing", progress=25, message="Streaming first CRC window")

    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC1, 0x00, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x04, 0x01, 0x74, 0x80], timeout=10000)
    send_can(canid=0x001, data=[0x18, 0x01] + key_0x18_2)
    VCU_response(canid=0x002, prefix=[0x18, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x00])
    VCU_response(canid=0x002, prefix=[0x0D, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x00, 0x00, 0x7C])
    VCU_response(canid=0x002, data=[0x10, 0x01, 0x80, 0x74, 0xC7, 0x83], timeout=1500)

    _emit(on_event, stage="finalize", phase="finalizing", progress=45, message="Repeating validation sequence")

    send_can(canid=0x001, data=[0x18, 0x01] + key_0x18)
    VCU_response(canid=0x002, prefix=[0x18, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, prefix=[0x0D, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x04, 0x01, 0x00, 0xC0, 0x7F, 0x80, 0x80])
    VCU_response(canid=0x002, data=[0x04, 0x01, 0x00, 0x00], timeout=10000)

    token = ctrl.session_token

    _emit(on_event, stage="finalize", phase="finalizing", progress=60, message="Applying authentication keys")

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)
    send_can(canid=0x001, data=key_0x19_2)
    VCU_response(canid=0x002, data=[0x19, 0x01, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x00])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)
    send_can(canid=0x001, data=key_0x19_1)
    VCU_response(canid=0x002, data=[0x19, 0x01, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)

    _emit(on_event, stage="finalize", phase="finalizing", progress=78, message="Running CRC verification")

    send_can(canid=0x001, data=[0x18, 0x01] + key_0x18)
    VCU_response(canid=0x002, data=[0x18, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x0D, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=60000)

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)
    send_can(canid=0x001, data=key_0x19_2)
    VCU_response(canid=0x002, data=[0x19, 0x01, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x00])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)

    _emit(on_event, stage="finalize", phase="finalizing", progress=90, message="Final authentication pass")

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)
    send_can(canid=0x001, data=key_0x19_1)
    VCU_response(canid=0x002, data=[0x19, 0x01, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)

    send_can(canid=0x001, data=[0x18, 0x01] + key_0x18)
    VCU_response(canid=0x002, data=[0x18, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x0D, 0x01, 0x00, 0xC1, 0x00, 0x80])
    VCU_response(canid=0x002, data=[0x0D, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x10, 0x01, 0x00, 0x01, 0xDE, 0x00])
    VCU_response(canid=0x002, prefix=[0x10, 0x01], timeout=60000)

    send_can(canid=0x001, data=[0x11, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x01])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)
    send_can(canid=0x001, data=key_0x19_2)
    VCU_response(canid=0x002, data=[0x19, 0x01, 0x01], timeout=1500)
    send_can(canid=0x001, data=[0x11, 0x01] + token + [0x00])
    VCU_response(canid=0x002, data=[0x11, 0x01] + token, timeout=1500)

    _emit(on_event, stage="finalize", phase="finalizing", progress=100, message="Finalization successful")
    return {"status": "success"}
