from intelhex import IntelHex

def hex_length(self, ih: IntelHex):
    FLASH_BASE = 0xC10000
    HEADER_SIZE = 0x80
    APP_SIZE_OFF = 0x14  # little-endian body size in APDB header

    body_len = (
            ih[FLASH_BASE + APP_SIZE_OFF + 0]
            | (ih[FLASH_BASE + APP_SIZE_OFF + 1] << 8)
            | (ih[FLASH_BASE + APP_SIZE_OFF + 2] << 16)
            | (ih[FLASH_BASE + APP_SIZE_OFF + 3] << 24)
    )

    total_len = body_len + HEADER_SIZE

    span_len = ih.maxaddr() - FLASH_BASE + 1
    if total_len != span_len:
        raise ValueError(
            f"header-derived length 0x{total_len:X} != actual span 0x{span_len:X}"
        )

    return total_len

print(hex_length(IntelHex("/Users/tanishqtyagi/TTCReverseProtocl/Code/latest/231_80kw.hex")))