# TTC Enc32/Dec32 (bit-serial transform)
# Constant key observed in your traces/decompile:
TTC_KEY = 0x6088569B
POLY    = 0x04C11DB7  # CRC-32 polynomial used in the shift/xor

def _roquhre3ma(x: int, mode: int, key: int = TTC_KEY) -> int:
    """
    mode == 0 -> encode (Enc32)
    mode == 1 -> decode (Dec32)
    Works on uint32, bit-serial (LSB-first) exactly like the C#.
    """
    x &= 0xFFFFFFFF
    key &= 0xFFFFFFFF
    out = 0

    for i in range(32):
        state_bit = key & 1
        input_bit = (x >> i) & 1
        out_bit = state_bit ^ input_bit
        out |= (out_bit << i)

        key >>= 1
        feedback = out_bit if mode == 0 else input_bit
        if feedback:
            key ^= POLY
        key &= 0xFFFFFFFF

    return out & 0xFFFFFFFF

def enc32(raw_u32: int, key: int = TTC_KEY) -> int:
    return _roquhre3ma(raw_u32, mode=0, key=key)

def dec32(enc_u32: int, key: int = TTC_KEY) -> int:
    return _roquhre3ma(enc_u32, mode=1, key=key)

# Helpers for CAN payload bytes (on-wire is big-endian uint32 in your traces)
def enc32_bytes(raw4: bytes, key: int = TTC_KEY) -> bytes:
    raw = int.from_bytes(raw4, "big", signed=False)
    return enc32(raw, key).to_bytes(4, "big", signed=False)

def dec32_bytes(enc4: bytes, key: int = TTC_KEY) -> bytes:
    encv = int.from_bytes(enc4, "big", signed=False)
    return dec32(encv, key).to_bytes(4, "big", signed=False)

if __name__ == "__main__":
    # Sanity checks from your trace
    input_bytes = bytes.fromhex("F5 69 5A 48")
    result_bytes = dec32_bytes(input_bytes)
    print(result_bytes.hex().upper())
    print("OK")