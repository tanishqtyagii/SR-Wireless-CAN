from intelhex import IntelHex
from typing import List
from CAN_controller import CANController


def return_header(ctrl, ih: IntelHex) -> List[int]:
    FLASH_BASE = 0xC10000
    HEADER_SIZE = 0x80
    APP_START_ADDR = FLASH_BASE + HEADER_SIZE

    APDB_VERSION = 0x02060000
    HW_TYPE = 0x00000001
    NODE_NUMBER = 0x00000001
    APP_START = 0x00C10080

    cls = type(ctrl)

    hex_length = ctrl.hex_length
    get_main_address = cls.get_main_address
    magic_seed_checksum = cls.magic_seed_checksum
    pack_time = cls.pack_time
    ttc_crc32 = cls.ttc_crc32
    dec32 = cls.dec32

    def read_u8(offset: int) -> int:
        return ih[FLASH_BASE + offset] & 0xFF

    def read_u16le(offset: int) -> int:
        return (
            read_u8(offset + 0)
            | (read_u8(offset + 1) << 8)
        ) & 0xFFFF

    def read_u32le(offset: int) -> int:
        return (
            read_u8(offset + 0)
            | (read_u8(offset + 1) << 8)
            | (read_u8(offset + 2) << 16)
            | (read_u8(offset + 3) << 24)
        ) & 0xFFFFFFFF

    def write_u16le(buf: List[int], offset: int, value: int) -> None:
        value &= 0xFFFF
        buf[offset + 0] = value & 0xFF
        buf[offset + 1] = (value >> 8) & 0xFF

    def write_u32le(buf: List[int], offset: int, value: int) -> None:
        value &= 0xFFFFFFFF
        buf[offset + 0] = value & 0xFF
        buf[offset + 1] = (value >> 8) & 0xFF
        buf[offset + 2] = (value >> 16) & 0xFF
        buf[offset + 3] = (value >> 24) & 0xFF

    header80 = [read_u8(i) for i in range(HEADER_SIZE)]

    FLASH_DATE = pack_time()
    BUILD_DATE = read_u32le(0x08)

    HEX_FILE_LENGTH = hex_length(ih)
    APP_SIZE = HEX_FILE_LENGTH - HEADER_SIZE
    APP_BYTES = bytes(ih[APP_START_ADDR + i] for i in range(APP_SIZE))

    LEGACY_APP_CRC = ttc_crc32(APP_BYTES, 0xFFFFFFFF)

    KEY_0X19_VALUE = int.from_bytes(bytes(ctrl.key_0x19_1[2:6]), "big")
    SESSION_SEED = dec32(KEY_0X19_VALUE)
    SESSION_APP_CRC = ttc_crc32(APP_BYTES, SESSION_SEED)

    FLAGS = read_u32le(0x28)
    SW_HOOK_1 = read_u32le(0x2C)
    SW_HOOK_2 = read_u32le(0x30)
    SW_HOOK_3 = read_u32le(0x34)

    MAIN_ADDRESS = int.from_bytes(bytes(get_main_address(ih)), "little")

    CAN_DL_ID_EXTENDED = read_u32le(0x3C)
    CAN_DL_ID = read_u32le(0x40)
    CAN_UL_ID_EXTENDED = read_u32le(0x44)
    CAN_UL_ID = read_u32le(0x48)

    APP_VERSION = read_u32le(0x50)
    BAUD = read_u32le(0x54)
    CAN_CHANNEL = read_u32le(0x58)
    PASSWORD_ENC = read_u32le(0x5C)

    MAGIC_SEED = magic_seed_checksum(APP_BYTES)

    TARGET_IP = read_u32le(0x64)
    IP_SUBNET_MASK = read_u32le(0x68)
    DOWNLOADER_MULTICAST_IP = read_u32le(0x6C)
    DEBUG_KEY = read_u32le(0x70)
    ABRD_TIMEOUT = read_u32le(0x74)

    MANUFACTURER_ID = read_u8(0x78)
    APPLICATION_ID = read_u8(0x79)
    EXTENDED_APDB_SIZE = read_u16le(0x7A)

    write_u32le(header80, 0x00, APDB_VERSION)
    write_u32le(header80, 0x04, FLASH_DATE)
    write_u32le(header80, 0x08, BUILD_DATE)
    write_u32le(header80, 0x0C, HW_TYPE)
    write_u32le(header80, 0x10, APP_START)
    write_u32le(header80, 0x14, APP_SIZE)
    write_u32le(header80, 0x18, LEGACY_APP_CRC)
    write_u32le(header80, 0x1C, SESSION_APP_CRC)
    write_u32le(header80, 0x20, NODE_NUMBER)
    write_u32le(header80, 0x24, KEY_0X19_VALUE)
    write_u32le(header80, 0x28, FLAGS)
    write_u32le(header80, 0x2C, SW_HOOK_1)
    write_u32le(header80, 0x30, SW_HOOK_2)
    write_u32le(header80, 0x34, SW_HOOK_3)
    write_u32le(header80, 0x38, MAIN_ADDRESS)
    write_u32le(header80, 0x3C, CAN_DL_ID_EXTENDED)
    write_u32le(header80, 0x40, CAN_DL_ID)
    write_u32le(header80, 0x44, CAN_UL_ID_EXTENDED)
    write_u32le(header80, 0x48, CAN_UL_ID)

    LEGACY_HEADER_CRC = ttc_crc32(bytes(header80[0x00:0x4C]), 0xFFFFFFFF)
    write_u32le(header80, 0x4C, LEGACY_HEADER_CRC)

    write_u32le(header80, 0x50, APP_VERSION)
    write_u32le(header80, 0x54, BAUD)
    write_u32le(header80, 0x58, CAN_CHANNEL)
    write_u32le(header80, 0x5C, PASSWORD_ENC)
    write_u32le(header80, 0x60, MAGIC_SEED)
    write_u32le(header80, 0x64, TARGET_IP)
    write_u32le(header80, 0x68, IP_SUBNET_MASK)
    write_u32le(header80, 0x6C, DOWNLOADER_MULTICAST_IP)
    write_u32le(header80, 0x70, DEBUG_KEY)
    write_u32le(header80, 0x74, ABRD_TIMEOUT)
    header80[0x78] = MANUFACTURER_ID & 0xFF
    header80[0x79] = APPLICATION_ID & 0xFF
    write_u16le(header80, 0x7A, EXTENDED_APDB_SIZE)

    APDB_HEADER_CRC = ttc_crc32(bytes(header80[0x00:0x7C]), 0xFFFFFFFF)
    write_u32le(header80, 0x7C, APDB_HEADER_CRC)

    return header80