"""
tests/test_ir_decoder.py — Unit tests for ir_decoder.py.

All tests work without the Logic 2 / saleae SDK: only ir_decoder is imported.
Synthetic timing vectors are generated from known protocol parameters so the
expected decoded fields can be verified exactly.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from ir_decoder import (
    DecodeResult,
    decode_ir_raw,
    decode_jvc,
    decode_lg,
    decode_nec,
    decode_panasonic,
    decode_rc5,
    decode_samsung,
    decode_samsung36,
    decode_sharp,
    decode_sony,
    match_us,
)

# ---------------------------------------------------------------------------
# Timing-vector generators
# ---------------------------------------------------------------------------

def _pdm_frame(header_mark: int, header_space: int,
               bit_mark: int, one_space: int, zero_space: int,
               stop_mark: int, value: int, nbits: int) -> list:
    """Build a raw PDM timing sequence from protocol parameters."""
    raw = [header_mark, header_space]
    for i in range(nbits):
        bit = (value >> i) & 1
        raw.append(bit_mark)
        raw.append(one_space if bit else zero_space)
    raw.append(stop_mark)
    return raw


def make_nec_raw(address: int, command: int) -> list:
    """Generate NEC timing sequence (µs) for the given address/command."""
    addr_inv = (~address) & 0xFF
    cmd_inv  = (~command) & 0xFF
    value = (address & 0xFF) | (addr_inv << 8) | ((command & 0xFF) << 16) | (cmd_inv << 24)
    return _pdm_frame(9000, 4500, 560, 1690, 560, 560, value, 32)


def make_necx_raw(address: int, command: int) -> list:
    """Generate NEC Extended timing sequence (16-bit address)."""
    cmd_inv = (~command) & 0xFF
    value = (address & 0xFFFF) | ((command & 0xFF) << 16) | (cmd_inv << 24)
    return _pdm_frame(9000, 4500, 560, 1690, 560, 560, value, 32)


def make_samsung_raw(address: int, command: int) -> list:
    value = (address & 0xFFFF) | ((command & 0xFFFF) << 16)
    return _pdm_frame(4500, 4500, 560, 1690, 560, 560, value, 32)


def make_samsung36_raw(address: int, command: int) -> list:
    value = (address & 0xFFFF) | ((command & 0xFFFFF) << 16)
    return _pdm_frame(4500, 4500, 560, 1690, 560, 560, value, 36)


def make_sony_raw(address: int, command: int, bits: int = 12) -> list:
    """Generate Sony SIRC timing sequence."""
    if bits == 12:
        value = (command & 0x7F) | ((address & 0x1F)   << 7)
    elif bits == 15:
        value = (command & 0x7F) | ((address & 0xFF)   << 7)
    else:  # 20
        value = (command & 0x7F) | ((address & 0x1FFF) << 7)

    raw = [2400, 600]
    for i in range(bits):
        bit = (value >> i) & 1
        raw.append(1200 if bit else 600)   # mark
        if i < bits - 1:
            raw.append(600)                # space (no trailing space)
    return raw


def make_lg_raw(address: int, command: int) -> list:
    value = ((address & 0xFF) << 20) | ((command & 0xFFFF) << 4)
    # Simple 4-bit checksum (not validated by decoder, just fill with 0)
    return _pdm_frame(8500, 4250, 560, 1600, 560, 560, value, 28)


def make_jvc_raw(address: int, command: int) -> list:
    value = (address & 0xFF) | ((command & 0xFF) << 8)
    return _pdm_frame(8400, 4200, 525, 1575, 525, 525, value, 16)


def make_panasonic_raw(manufacturer_id: int, address: int,
                       command: int) -> list:
    value = (
        (manufacturer_id & 0xFFFF) << 32
        | (address & 0xFFFF) << 16
        | (command & 0xFFFF)
    )
    return _pdm_frame(3500, 1750, 432, 1296, 432, 432, value, 48)


def make_sharp_raw(address: int, command: int) -> list:
    value = (address & 0x1F) | ((command & 0xFF) << 5)
    # No header; stop bit included via the last space of last bit
    raw = []
    for i in range(15):
        bit = (value >> i) & 1
        raw.append(320)
        raw.append(1000 if bit else 680)
    return raw


def make_rc5_raw(address: int, command: int, toggle: int = 0) -> list:
    """Generate RC5 Manchester-encoded timing sequence."""
    RC5_T1 = 889
    bits = (
        [1, 1, toggle]
        + [(address >> (4 - i)) & 1 for i in range(5)]
        + [(command >> (5 - i)) & 1 for i in range(6)]
    )
    # Convert to half-period level sequence
    half_periods = []
    for b in bits:
        if b:
            half_periods.extend([1, 0])   # HIGH then LOW
        else:
            half_periods.extend([0, 1])   # LOW then HIGH
    # Compress consecutive same-level half-periods into single timing
    raw = []
    i = 0
    while i < len(half_periods):
        count = 1
        while (i + count < len(half_periods)
               and half_periods[i + count] == half_periods[i]):
            count += 1
        raw.append(count * RC5_T1)
        i += count
    return raw


# ---------------------------------------------------------------------------
# match_us
# ---------------------------------------------------------------------------

class TestMatchUs:
    def test_exact(self):
        assert match_us(560, 560) is True

    def test_within_tolerance(self):
        # 30 % tolerance; 560 * 0.7 = 392, 560 * 1.3 = 728
        assert match_us(392, 560) is True
        assert match_us(728, 560) is True

    def test_outside_tolerance(self):
        assert match_us(391, 560) is False
        assert match_us(729, 560) is False

    def test_custom_tolerance(self):
        assert match_us(560, 560, tol=0.0) is True
        assert match_us(561, 560, tol=0.0) is False

    def test_zero_expected(self):
        # When expected == 0 the result is True only if actual == 0
        assert match_us(0, 0) is True
        assert match_us(1, 0) is False


# ---------------------------------------------------------------------------
# NEC
# ---------------------------------------------------------------------------

class TestNEC:
    def test_basic_decode(self):
        raw = make_nec_raw(0x00, 0xFF)
        r = decode_nec(raw)
        assert r is not None
        assert r.protocol == "NEC"
        assert r.manufacturer == "NEC"
        assert r.address == 0x00
        assert r.command == 0xFF
        assert r.bits == 32

    def test_address_nonzero(self):
        raw = make_nec_raw(0xA5, 0x3C)
        r = decode_nec(raw)
        assert r is not None
        assert r.address == 0xA5
        assert r.command == 0x3C

    def test_full_address_range(self):
        for addr in (0x00, 0x01, 0x7F, 0xFF):
            raw = make_nec_raw(addr, 0x10)
            r = decode_nec(raw)
            assert r is not None, f"addr={addr:#x} failed"
            assert r.address == addr

    def test_too_short(self):
        assert decode_nec([9000, 4500, 560]) is None

    def test_wrong_header_mark(self):
        raw = make_nec_raw(0x00, 0xFF)
        raw[0] = 5000
        assert decode_nec(raw) is None

    def test_wrong_header_space(self):
        raw = make_nec_raw(0x00, 0xFF)
        raw[1] = 1000
        assert decode_nec(raw) is None

    def test_wrong_bit_timing(self):
        raw = make_nec_raw(0x00, 0xFF)
        raw[2] = 9000   # corrupt first bit mark
        assert decode_nec(raw) is None

    def test_value_encoding(self):
        # Verify the full 32-bit value round-trip
        raw = make_nec_raw(0x12, 0x34)
        r = decode_nec(raw)
        assert r is not None
        # value LSB = addr, bits 8-15 = ~addr, bits 16-23 = cmd, bits 24-31 = ~cmd
        assert (r.value & 0xFF) == 0x12
        assert ((r.value >> 8) & 0xFF) == (~0x12 & 0xFF)
        assert ((r.value >> 16) & 0xFF) == 0x34


class TestNECExtended:
    def test_necx_decode(self):
        raw = make_necx_raw(0xABCD, 0x7F)
        r = decode_nec(raw)
        assert r is not None
        assert r.protocol == "NECX"
        assert r.manufacturer == "NEC Extended"
        assert r.address == 0xABCD
        assert r.command == 0x7F


# ---------------------------------------------------------------------------
# Samsung
# ---------------------------------------------------------------------------

class TestSamsung:
    def test_basic_decode(self):
        raw = make_samsung_raw(0xE0E0, 0x40BF)
        r = decode_samsung(raw)
        assert r is not None
        assert r.protocol == "SAMSUNG"
        assert r.manufacturer == "Samsung"
        assert r.address == 0xE0E0
        assert r.command == 0x40BF

    def test_different_address(self):
        raw = make_samsung_raw(0x0707, 0x09F6)
        r = decode_samsung(raw)
        assert r is not None
        assert r.address == 0x0707

    def test_nec_rejects_samsung_header(self):
        # Samsung header (4500/4500) should not match NEC (9000/4500)
        raw = make_samsung_raw(0xE0E0, 0x40BF)
        assert decode_nec(raw) is None

    def test_samsung_rejects_nec_header(self):
        raw = make_nec_raw(0x00, 0xFF)
        assert decode_samsung(raw) is None

    def test_too_short(self):
        assert decode_samsung([4500, 4500]) is None


# ---------------------------------------------------------------------------
# Samsung36
# ---------------------------------------------------------------------------

class TestSamsung36:
    def test_basic_decode(self):
        raw = make_samsung36_raw(0xE0E0, 0xA40BF)
        r = decode_samsung36(raw)
        assert r is not None
        assert r.protocol == "SAMSUNG36"
        assert r.manufacturer == "Samsung"
        assert r.address == 0xE0E0
        assert r.command == 0xA40BF
        assert r.bits == 36

    def test_address_and_command_fields(self):
        raw = make_samsung36_raw(0x1234, 0xABCDE)
        r = decode_samsung36(raw)
        assert r is not None
        assert r.address == 0x1234
        assert r.command == 0xABCDE

    def test_value_encoding(self):
        raw = make_samsung36_raw(0x0001, 0x00001)
        r = decode_samsung36(raw)
        assert r is not None
        assert (r.value & 0xFFFF) == 0x0001
        assert ((r.value >> 16) & 0xFFFFF) == 0x00001

    def test_rejects_samsung32_frame(self):
        # Samsung32 frame has only 67 raw entries, fewer than the 75 required
        raw = make_samsung_raw(0xE0E0, 0x40BF)
        assert len(raw) == 67
        assert decode_samsung36(raw) is None

    def test_samsung32_not_misidentified(self):
        # The dispatcher must return SAMSUNG (32-bit) for a 32-bit frame
        raw = make_samsung_raw(0xE0E0, 0x40BF)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "SAMSUNG"

    def test_wrong_header_mark(self):
        raw = make_samsung36_raw(0xE0E0, 0xA40BF)
        raw[0] = 9000
        assert decode_samsung36(raw) is None

    def test_wrong_header_space(self):
        raw = make_samsung36_raw(0xE0E0, 0xA40BF)
        raw[1] = 1000
        assert decode_samsung36(raw) is None

    def test_too_short(self):
        assert decode_samsung36([4500, 4500]) is None


# ---------------------------------------------------------------------------
# Sony SIRC
# ---------------------------------------------------------------------------

class TestSony:
    def test_sirc12_decode(self):
        raw = make_sony_raw(0x01, 0x15, bits=12)
        r = decode_sony(raw)
        assert r is not None
        assert r.protocol == "SONY_SIRC12"
        assert r.manufacturer == "Sony"
        assert r.address == 0x01
        assert r.command == 0x15
        assert r.bits == 12

    def test_sirc15_decode(self):
        raw = make_sony_raw(0x0A, 0x20, bits=15)
        r = decode_sony(raw)
        assert r is not None
        assert r.protocol == "SONY_SIRC15"
        assert r.bits == 15

    def test_sirc20_decode(self):
        raw = make_sony_raw(0x0001, 0x3A, bits=20)
        r = decode_sony(raw)
        assert r is not None
        assert r.protocol == "SONY_SIRC20"
        assert r.bits == 20

    def test_wrong_header(self):
        raw = make_sony_raw(0x01, 0x15)
        raw[0] = 9000  # NEC-like header
        assert decode_sony(raw) is None

    def test_too_short(self):
        assert decode_sony([2400, 600]) is None


# ---------------------------------------------------------------------------
# LG
# ---------------------------------------------------------------------------

class TestLG:
    def test_basic_decode(self):
        raw = make_lg_raw(0x04, 0x8000)
        r = decode_lg(raw)
        assert r is not None
        assert r.protocol == "LG"
        assert r.manufacturer == "LG"

    def test_header_mismatch(self):
        raw = make_lg_raw(0x04, 0x8000)
        raw[0] = 3000  # clearly outside ±30 % of 8500 µs
        assert decode_lg(raw) is None

    def test_too_short(self):
        assert decode_lg([8500]) is None


# ---------------------------------------------------------------------------
# JVC
# ---------------------------------------------------------------------------

class TestJVC:
    def test_basic_decode(self):
        raw = make_jvc_raw(0x03, 0x7A)
        r = decode_jvc(raw)
        assert r is not None
        assert r.protocol == "JVC"
        assert r.manufacturer == "JVC"
        assert r.address == 0x03
        assert r.command == 0x7A

    def test_wrong_header(self):
        raw = make_jvc_raw(0x03, 0x7A)
        raw[0] = 3000  # clearly outside ±30 % of 8400 µs
        assert decode_jvc(raw) is None

    def test_too_short(self):
        assert decode_jvc([8400]) is None


# ---------------------------------------------------------------------------
# Panasonic / Kaseikyo
# ---------------------------------------------------------------------------

class TestPanasonic:
    def test_panasonic_decode(self):
        raw = make_panasonic_raw(0x4004, 0x0220, 0x0100)
        r = decode_panasonic(raw)
        assert r is not None
        assert r.protocol == "PANASONIC"
        assert r.manufacturer == "Panasonic"
        assert r.address == 0x0220
        assert r.command == 0x0100

    def test_denon_kaseikyo(self):
        raw = make_panasonic_raw(0x0000, 0x0001, 0x0002)
        r = decode_panasonic(raw)
        assert r is not None
        assert r.manufacturer == "Denon"

    def test_unknown_manufacturer_id(self):
        raw = make_panasonic_raw(0x1234, 0x0001, 0x0002)
        r = decode_panasonic(raw)
        assert r is not None
        assert "1234" in r.manufacturer

    def test_wrong_header(self):
        raw = make_panasonic_raw(0x4004, 0x0220, 0x0100)
        raw[0] = 9000
        assert decode_panasonic(raw) is None

    def test_too_short(self):
        assert decode_panasonic([3500]) is None


# ---------------------------------------------------------------------------
# Sharp
# ---------------------------------------------------------------------------

class TestSharp:
    def test_basic_decode(self):
        raw = make_sharp_raw(0x02, 0xA0)
        r = decode_sharp(raw)
        assert r is not None
        assert r.protocol == "SHARP"
        assert r.manufacturer == "Sharp"
        assert r.address == 0x02
        assert r.command == 0xA0

    def test_wrong_bit_mark(self):
        raw = make_sharp_raw(0x02, 0xA0)
        raw[0] = 9000
        assert decode_sharp(raw) is None

    def test_too_short(self):
        assert decode_sharp([320]) is None


# ---------------------------------------------------------------------------
# RC5
# ---------------------------------------------------------------------------

class TestRC5:
    def test_basic_decode(self):
        raw = make_rc5_raw(address=5, command=15, toggle=0)
        r = decode_rc5(raw)
        assert r is not None
        assert r.protocol == "RC5"
        assert r.manufacturer == "Philips RC5"
        assert r.address == 5
        assert r.command == 15
        assert r.bits == 14

    def test_toggle_bit(self):
        raw0 = make_rc5_raw(address=1, command=1, toggle=0)
        raw1 = make_rc5_raw(address=1, command=1, toggle=1)
        r0 = decode_rc5(raw0)
        r1 = decode_rc5(raw1)
        assert r0 is not None and r1 is not None
        assert r0.extra["toggle"] == 0
        assert r1.extra["toggle"] == 1

    def test_address_and_command_range(self):
        for addr in (0, 1, 15, 31):
            for cmd in (0, 1, 31, 63):
                raw = make_rc5_raw(addr, cmd)
                r = decode_rc5(raw)
                assert r is not None, f"addr={addr} cmd={cmd} failed"
                assert r.address == addr, f"addr mismatch: {r.address} != {addr}"
                assert r.command == cmd, f"cmd mismatch: {r.command} != {cmd}"

    def test_invalid_timing(self):
        # Timings that are neither T1 nor 2*T1
        assert decode_rc5([9000, 4500] * 10) is None

    def test_too_short(self):
        assert decode_rc5([889] * 5) is None


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

class TestDecodeIRRaw:
    def test_dispatches_nec(self):
        raw = make_nec_raw(0x04, 0x08)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "NEC"

    def test_dispatches_samsung(self):
        raw = make_samsung_raw(0xE0E0, 0x40BF)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "SAMSUNG"

    def test_dispatches_samsung36(self):
        raw = make_samsung36_raw(0xE0E0, 0xA40BF)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "SAMSUNG36"

    def test_dispatches_sony(self):
        raw = make_sony_raw(0x01, 0x15, bits=12)
        r = decode_ir_raw(raw)
        assert r is not None
        assert "SONY" in r.protocol

    def test_dispatches_lg(self):
        raw = make_lg_raw(0x04, 0x8000)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "LG"

    def test_dispatches_jvc(self):
        raw = make_jvc_raw(0x03, 0x7A)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "JVC"

    def test_dispatches_panasonic(self):
        raw = make_panasonic_raw(0x4004, 0x0220, 0x0100)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "PANASONIC"

    def test_dispatches_sharp(self):
        raw = make_sharp_raw(0x02, 0xA0)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "SHARP"

    def test_dispatches_rc5(self):
        raw = make_rc5_raw(address=5, command=15)
        r = decode_ir_raw(raw)
        assert r is not None
        assert r.protocol == "RC5"

    def test_returns_none_for_garbage(self):
        assert decode_ir_raw([100, 200, 300] * 5) is None

    def test_returns_none_for_empty(self):
        assert decode_ir_raw([]) is None


# ---------------------------------------------------------------------------
# DecodeResult __str__
# ---------------------------------------------------------------------------

class TestDecodeResultStr:
    def test_str_format(self):
        r = DecodeResult(
            protocol="NEC",
            manufacturer="NEC",
            value=0x00FF807F,
            address=0x00,
            command=0xFF,
            bits=32,
        )
        s = str(r)
        assert "NEC" in s
        assert "addr=0x0000" in s
        assert "cmd=0x00FF" in s
        assert "32b" in s
