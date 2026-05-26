from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any, Callable

from backend.hardware_runtime import bootload_with_progress, finalize_with_progress, flash_hex_with_progress
from backend.settings import Settings
from backend.utils import load_hex_lenient, safe_json

ProgressCallback = Callable[[dict[str, Any]], None]
WaitForImdCallback = Callable[[], bool]


class FirmwareFlasher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run_bootload_only(self, *, on_event: ProgressCallback) -> dict[str, Any]:
        if self.settings.flash_simulate:
            return self._simulate_bootload_only(on_event=on_event)
        return self._actual_bootload_only(on_event=on_event)

    def run_boot_and_flash(
        self,
        *,
        file_path: str | Path,
        on_event: ProgressCallback,
        wait_for_imd: WaitForImdCallback,
    ) -> dict[str, Any]:
        if self.settings.flash_simulate:
            return self._simulate_boot_and_flash(file_path=file_path, on_event=on_event, wait_for_imd=wait_for_imd)
        return self._actual_boot_and_flash(file_path=file_path, on_event=on_event, wait_for_imd=wait_for_imd)

    def run_flash_only(
        self,
        *,
        file_path: str | Path,
        on_event: ProgressCallback,
    ) -> dict[str, Any]:
        if self.settings.flash_simulate:
            return self._simulate_flash_only(file_path=file_path, on_event=on_event)
        return self._actual_flash_only(file_path=file_path, on_event=on_event)

    @staticmethod
    def _emit(on_event: ProgressCallback, **payload: Any) -> None:
        payload = {key: safe_json(value) for key, value in payload.items() if value is not None}
        on_event(payload)

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    # ── Simulation mode ───────────────────────────────────────────────────────

    def _simulate_bootload(self, *, on_event: ProgressCallback) -> None:
        steps = [
            (5, "Sending bootloader wake frames", False),
            (25, "Requesting power cycle", True),
            (45, "Waiting for VCU restart", True),
            (70, "Negotiating diagnostic session", False),
            (88, "Completing bootloader authentication", False),
            (100, "Bootloader acknowledged", False),
        ]
        for progress, message, power_cycle in steps:
            self._emit(
                on_event,
                stage="bootload",
                phase="bootloading",
                progress=progress,
                message=message,
                powerCycle=power_cycle,
            )
            self._sleep(0.12 if progress < 100 else 0.05)

    def _simulate_flash_kernel(self, *, on_event: ProgressCallback) -> None:
        for progress, message in [(0, "Preparing flash kernel"), (50, "Installing flash kernel"), (100, "Flash kernel ready")]:
            self._emit(on_event, stage="flash_kernel", phase="flash_kernel", progress=progress, message=message)
            self._sleep(0.08)

    def _simulate_erase(self, *, file_size: int, on_event: ProgressCallback) -> None:
        chunks = max(1, min(6, math.ceil(max(file_size, 1) / 65536)))
        for idx in range(chunks):
            progress = ((idx + 1) / chunks) * 100.0
            self._emit(
                on_event,
                stage="erase",
                phase="erasing",
                progress=round(progress, 1),
                message=f"Erasing flash region {idx + 1}/{chunks}",
                detail={"chunk": idx + 1, "chunks": chunks},
            )
            self._sleep(0.06)

    def _simulate_flash_hex(self, *, file_size: int, on_event: ProgressCallback) -> dict[str, Any]:
        block_size = 0x8000
        total_blocks = max(1, math.ceil(max(file_size, 1) / block_size))
        written = 0
        for block_index in range(total_blocks):
            chunk = min(block_size, max(file_size, block_size) - written)
            written += chunk
            progress = ((block_index + 1) / total_blocks) * 100.0
            self._emit(
                on_event,
                stage="flash_hex",
                phase="flashing_hex",
                progress=round(progress, 1),
                message=f"Flashing block {block_index + 1}/{total_blocks}",
                detail={
                    "blockIndex": block_index + 1,
                    "totalBlocks": total_blocks,
                    "bytesWritten": written,
                    "totalBytes": file_size,
                },
            )
            self._sleep(0.04)
        return {
            "status": "success",
            "flash_base": hex(0xC10000),
            "total_len": file_size,
            "blocks": total_blocks,
            "last_block_len": file_size % block_size or block_size,
            "erased": bool(self.settings.flash_do_erase),
            "kernel": True,
            "simulated": True,
        }

    def _simulate_finalize(self, *, on_event: ProgressCallback) -> None:
        for progress, message in [(0, "Starting finalization"), (35, "Verifying CRCs"), (70, "Finishing VCU handshake"), (100, "Finalization complete")]:
            self._emit(on_event, stage="finalize", phase="finalizing", progress=progress, message=message)
            self._sleep(0.06)

    def _simulate_bootload_only(self, *, on_event: ProgressCallback) -> dict[str, Any]:
        self._emit(on_event, stage="validation", phase="preparing", progress=100, message="Bootload-only job accepted")
        self._simulate_bootload(on_event=on_event)
        return {"status": "success", "simulated": True}

    def _simulate_boot_and_flash(
        self,
        *,
        file_path: str | Path,
        on_event: ProgressCallback,
        wait_for_imd: WaitForImdCallback,
    ) -> dict[str, Any]:
        file_path = Path(file_path)
        file_size = file_path.stat().st_size if file_path.exists() else 0
        self._emit(on_event, stage="validation", phase="validating_file", progress=100, message="HEX validated")
        self._simulate_bootload(on_event=on_event)
        if self.settings.flash_require_imd_confirm:
            self._emit(on_event, stage="imd", phase="waiting_imd", progress=0, imdWaiting=True, message="Waiting for IMD confirmation")
            if not wait_for_imd():
                raise RuntimeError("IMD confirmation timed out. Aborting flash.")
            self._emit(on_event, stage="imd", phase="waiting_imd", progress=100, imdWaiting=False, message="IMD confirmed")
        self._simulate_flash_kernel(on_event=on_event)
        if self.settings.flash_do_erase:
            self._simulate_erase(file_size=file_size, on_event=on_event)
        result = self._simulate_flash_hex(file_size=file_size, on_event=on_event)
        self._simulate_finalize(on_event=on_event)
        return result

    def _simulate_flash_only(self, *, file_path: str | Path, on_event: ProgressCallback) -> dict[str, Any]:
        file_path = Path(file_path)
        file_size = file_path.stat().st_size if file_path.exists() else 0
        self._emit(on_event, stage="validation", phase="validating_file", progress=100, message="HEX validated")
        self._simulate_flash_kernel(on_event=on_event)
        if self.settings.flash_do_erase:
            self._simulate_erase(file_size=file_size, on_event=on_event)
        result = self._simulate_flash_hex(file_size=file_size, on_event=on_event)
        self._simulate_finalize(on_event=on_event)
        return result

    # ── Actual hardware mode ──────────────────────────────────────────────────

    def _import_flash_stack(self) -> tuple[Any, Any, Any, Any]:
        latest_dir = self.settings.root_dir / "latest"
        latest_dir_str = str(latest_dir)
        if latest_dir_str not in sys.path:
            sys.path.insert(0, latest_dir_str)
        try:
            from CAN_controller import CANController, VCUTimeoutError
            from flash_kernel import flash_kernel
            from return_header import return_header
        except ImportError as exc:  # pragma: no cover - host dependency
            raise RuntimeError(
                "Flashing dependencies are unavailable. Install python-can and intelhex on the host, or set FLASH_SIMULATE=1."
            ) from exc
        return CANController, VCUTimeoutError, flash_kernel, return_header

    def _actual_bootload_only(self, *, on_event: ProgressCallback) -> dict[str, Any]:
        CANController, VCUTimeoutError, _flash_kernel, _return_header = self._import_flash_stack()
        ctrl = CANController(interface=self.settings.flash_can_interface, channel=self.settings.flash_can_channel)
        try:
            return bootload_with_progress(ctrl, on_event=on_event, timeout_error=VCUTimeoutError)
        finally:
            ctrl.close()

    def _actual_boot_and_flash(
        self,
        *,
        file_path: str | Path,
        on_event: ProgressCallback,
        wait_for_imd: WaitForImdCallback,
    ) -> dict[str, Any]:
        CANController, VCUTimeoutError, flash_kernel, return_header = self._import_flash_stack()
        path = Path(file_path)
        ih = load_hex_lenient(path)
        self._emit(on_event, stage="validation", phase="validating_file", progress=100, message="HEX validated")

        ctrl = CANController(interface=self.settings.flash_can_interface, channel=self.settings.flash_can_channel)
        try:
            bootload_with_progress(ctrl, on_event=on_event, timeout_error=VCUTimeoutError)
            if self.settings.flash_require_imd_confirm:
                self._emit(on_event, stage="imd", phase="waiting_imd", progress=0, imdWaiting=True, message="Waiting for IMD confirmation")
                if not wait_for_imd():
                    raise RuntimeError("IMD confirmation timed out. Aborting flash.")
                self._emit(on_event, stage="imd", phase="waiting_imd", progress=100, imdWaiting=False, message="IMD confirmed")
            header80 = return_header(ctrl, ih)
            result = flash_hex_with_progress(
                ctrl,
                ih,
                header80,
                on_event=on_event,
                flash_kernel_func=flash_kernel,
                do_flash_kernel=True,
                do_erase=self.settings.flash_do_erase,
            )
            finalize_with_progress(ctrl, on_event=on_event)
            result = dict(result)
            result["simulated"] = False
            return result
        finally:
            ctrl.close()

    def _actual_flash_only(self, *, file_path: str | Path, on_event: ProgressCallback) -> dict[str, Any]:
        CANController, _VCUTimeoutError, flash_kernel, return_header = self._import_flash_stack()
        path = Path(file_path)
        ih = load_hex_lenient(path)
        self._emit(on_event, stage="validation", phase="validating_file", progress=100, message="HEX validated")

        ctrl = CANController(interface=self.settings.flash_can_interface, channel=self.settings.flash_can_channel)
        try:
            header80 = return_header(ctrl, ih)
            result = flash_hex_with_progress(
                ctrl,
                ih,
                header80,
                on_event=on_event,
                flash_kernel_func=flash_kernel,
                do_flash_kernel=True,
                do_erase=self.settings.flash_do_erase,
            )
            finalize_with_progress(ctrl, on_event=on_event)
            result = dict(result)
            result["simulated"] = False
            return result
        finally:
            ctrl.close()
