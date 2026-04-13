"""
ir_decoder.py — IRremoteESP8266-compatible IR protocol decoder.

Decodes raw IR timing sequences (alternating mark/space durations in
microseconds) into structured decode results with manufacturer name and
data payload.  Supported protocols mirror the IRremoteESP8266 library:
  NEC / NEC Extended, Samsung, Sony SIRC (12/15/20-bit), LG, JVC,
  Panasonic / Kaseikyo, Sharp, RC5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Timing utilities
# ---------------------------------------------------------------------------

# Default tolerance for timing comparisons (±30 %)
_DEFAULT_TOL = 0.30


def match_us(actual_us: float, expected_us: float,
             tol: float = _DEFAULT_TOL) -> bool:
    """Return True when *actual_us* is within *tol* of *expected_us*."""
    return abs(actual_us - expected_us) <= expected_us * tol


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DecodeResult:
    """Structured result from an IR decode attempt.

    Attributes
    ----------
    protocol:     Short protocol identifier string (e.g. ``"NEC"``).
    manufacturer: Human-readable manufacturer / protocol family name.
    value:        Full decoded integer value (all bits concatenated).
    address:      Device address field extracted from the protocol.
    command:      Command field extracted from the protocol.
    bits:         Number of data bits in the frame.
    raw_count:    Number of raw timing entries that were consumed.
    extra:        Protocol-specific metadata (e.g. RC5 toggle bit).
    """

    protocol: str
    manufacturer: str
    value: int
    address: int
    command: int
    bits: int
    raw_count: int = 0
    extra: dict = field(default_factory=dict)

    def __str__(self) -> str:
        hex_width = (self.bits + 3) // 4
        return (
            f"{self.manufacturer} [{self.protocol}] "
            f"addr=0x{self.address:04X} "
            f"cmd=0x{self.command:04X} "
            f"val=0x{self.value:0{hex_width}X} "
            f"({self.bits}b)"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_pdm_bits(raw: List[float], start: int, num_bits: int,
                     bit_mark_us: float, one_space_us: float,
                     zero_space_us: float,
                     tol: float = _DEFAULT_TOL) -> Optional[int]:
    """Decode *num_bits* using pulse-distance modulation (PDM).

    Each bit is encoded as: ``bit_mark`` followed by ``one_space`` (bit=1)
    or ``zero_space`` (bit=0).  Bits are returned LSB-first (bit 0 is the
    first bit received).

    Parameters
    ----------
    raw:          List of alternating mark/space durations in microseconds.
    start:        Index into *raw* of the first bit mark.
    num_bits:     Total number of bits to decode.
    bit_mark_us:  Expected duration (µs) of each bit mark.
    one_space_us: Expected space duration for a '1' bit.
    zero_space_us:Expected space duration for a '0' bit.

    Returns the decoded integer (LSB first), or ``None`` on failure.
    """
    value = 0
    idx = start
    for i in range(num_bits):
        if idx + 1 >= len(raw):
            return None
        if not match_us(raw[idx], bit_mark_us, tol):
            return None
        if match_us(raw[idx + 1], one_space_us, tol):
            value |= (1 << i)
        elif match_us(raw[idx + 1], zero_space_us, tol):
            pass  # bit stays 0
        else:
            return None
        idx += 2
    return value


# ---------------------------------------------------------------------------
# Protocol decoders
# ---------------------------------------------------------------------------

def decode_nec(raw: List[float]) -> Optional[DecodeResult]:
    """Decode NEC / NEC Extended protocol.

    Frame structure (all timings in µs):
      Header : 9000 mark + 4500 space
      32 bits LSB-first : addr(8) + ~addr(8) + cmd(8) + ~cmd(8)
      Bit    : 560 mark + 1690 space (1) / 560 space (0)
      Stop   : 560 mark
    """
    HDR_MARK   = 9000
    HDR_SPACE  = 4500
    BIT_MARK   = 560
    ONE_SPACE  = 1690
    ZERO_SPACE = 560
    NBITS      = 32

    # Minimum raw length: header(2) + 32 bit-pairs(64) + stop(1) = 67
    if len(raw) < 67:
        return None
    if not match_us(raw[0], HDR_MARK):
        return None
    if not match_us(raw[1], HDR_SPACE):
        return None

    value = _decode_pdm_bits(raw, 2, NBITS, BIT_MARK, ONE_SPACE, ZERO_SPACE)
    if value is None:
        return None

    # Validate / classify stop bit
    if not match_us(raw[66], BIT_MARK):
        return None

    addr_byte = value & 0xFF
    addr_inv  = (value >> 8) & 0xFF
    cmd_byte  = (value >> 16) & 0xFF
    cmd_inv   = (value >> 24) & 0xFF

    if (addr_byte ^ addr_inv) == 0xFF and (cmd_byte ^ cmd_inv) == 0xFF:
        # Standard NEC: 8-bit address + 8-bit command
        protocol     = "NEC"
        manufacturer = "NEC"
        address      = addr_byte
        command      = cmd_byte
    elif (cmd_byte ^ cmd_inv) == 0xFF:
        # NEC Extended: 16-bit address
        protocol     = "NECX"
        manufacturer = "NEC Extended"
        address      = value & 0xFFFF
        command      = cmd_byte
    else:
        return None

    return DecodeResult(
        protocol=protocol,
        manufacturer=manufacturer,
        value=value,
        address=address,
        command=command,
        bits=NBITS,
        raw_count=len(raw),
    )


def decode_samsung(raw: List[float]) -> Optional[DecodeResult]:
    """Decode Samsung protocol.

    Frame structure:
      Header : 4500 mark + 4500 space
      32 bits LSB-first
      Bit    : 560 mark + 1690 space (1) / 560 space (0)
      Stop   : 560 mark
    """
    HDR_MARK   = 4500
    HDR_SPACE  = 4500
    BIT_MARK   = 560
    ONE_SPACE  = 1690
    ZERO_SPACE = 560
    NBITS      = 32

    if len(raw) < 67:
        return None
    if not match_us(raw[0], HDR_MARK):
        return None
    if not match_us(raw[1], HDR_SPACE):
        return None

    value = _decode_pdm_bits(raw, 2, NBITS, BIT_MARK, ONE_SPACE, ZERO_SPACE)
    if value is None:
        return None

    if not match_us(raw[66], BIT_MARK):
        return None

    address = value & 0xFFFF
    command = (value >> 16) & 0xFFFF

    return DecodeResult(
        protocol="SAMSUNG",
        manufacturer="Samsung",
        value=value,
        address=address,
        command=command,
        bits=NBITS,
        raw_count=len(raw),
    )


def decode_sony(raw: List[float]) -> Optional[DecodeResult]:
    """Decode Sony SIRC protocol (12, 15 or 20-bit variants).

    Frame structure:
      Header : 2400 mark + 600 space
      N bits LSB-first
      Bit    : (1200 mark (1) / 600 mark (0)) + 600 space
      Last bit has no trailing space.
    """
    HDR_MARK   = 2400
    HDR_SPACE  = 600
    ONE_MARK   = 1200
    ZERO_MARK  = 600
    BIT_SPACE  = 600

    # Need at least header + 12 bits (no trailing space on last bit)
    # = 2 + 12*2 - 1 = 25 entries minimum
    if len(raw) < 25:
        return None
    if not match_us(raw[0], HDR_MARK):
        return None
    if not match_us(raw[1], HDR_SPACE):
        return None

    bits = []
    idx = 2
    while idx < len(raw):
        mark = raw[idx]
        if match_us(mark, ONE_MARK):
            bits.append(1)
        elif match_us(mark, ZERO_MARK):
            bits.append(0)
        else:
            break
        idx += 1
        # Space after every bit except the last
        if idx < len(raw) and match_us(raw[idx], BIT_SPACE):
            idx += 1
        else:
            break

    num_bits = len(bits)
    if num_bits not in (12, 15, 20):
        return None

    value = sum(b << i for i, b in enumerate(bits))

    if num_bits == 12:
        command = value & 0x7F
        address = (value >> 7) & 0x1F
        variant = "SIRC12"
    elif num_bits == 15:
        command = value & 0x7F
        address = (value >> 7) & 0xFF
        variant = "SIRC15"
    else:  # 20
        command = value & 0x7F
        address = (value >> 7) & 0x1FFF
        variant = "SIRC20"

    return DecodeResult(
        protocol=f"SONY_{variant}",
        manufacturer="Sony",
        value=value,
        address=address,
        command=command,
        bits=num_bits,
        raw_count=len(raw),
    )


def decode_lg(raw: List[float]) -> Optional[DecodeResult]:
    """Decode LG protocol (28-bit).

    Frame structure:
      Header : 8500 mark + 4250 space
      28 bits LSB-first : addr(8) + cmd(16) + checksum(4)
      Bit    : 560 mark + 1600 space (1) / 560 space (0)
      Stop   : 560 mark
    """
    HDR_MARK   = 8500
    HDR_SPACE  = 4250
    BIT_MARK   = 560
    ONE_SPACE  = 1600
    ZERO_SPACE = 560
    NBITS      = 28

    # header(2) + 28 bit-pairs(56) + stop(1) = 59
    if len(raw) < 59:
        return None
    if not match_us(raw[0], HDR_MARK):
        return None
    if not match_us(raw[1], HDR_SPACE):
        return None

    value = _decode_pdm_bits(raw, 2, NBITS, BIT_MARK, ONE_SPACE, ZERO_SPACE)
    if value is None:
        return None

    if not match_us(raw[58], BIT_MARK):
        return None

    address  = (value >> 20) & 0xFF
    command  = (value >> 4) & 0xFFFF
    checksum = value & 0xF

    return DecodeResult(
        protocol="LG",
        manufacturer="LG",
        value=value,
        address=address,
        command=command,
        bits=NBITS,
        raw_count=len(raw),
        extra={"checksum": checksum},
    )


def decode_jvc(raw: List[float]) -> Optional[DecodeResult]:
    """Decode JVC protocol (16-bit).

    Frame structure:
      Header : 8400 mark + 4200 space
      16 bits LSB-first : addr(8) + cmd(8)
      Bit    : 525 mark + 1575 space (1) / 525 space (0)
      Stop   : 525 mark
    """
    HDR_MARK   = 8400
    HDR_SPACE  = 4200
    BIT_MARK   = 525
    ONE_SPACE  = 1575
    ZERO_SPACE = 525
    NBITS      = 16

    # header(2) + 16 bit-pairs(32) + stop(1) = 35
    if len(raw) < 35:
        return None
    if not match_us(raw[0], HDR_MARK):
        return None
    if not match_us(raw[1], HDR_SPACE):
        return None

    value = _decode_pdm_bits(raw, 2, NBITS, BIT_MARK, ONE_SPACE, ZERO_SPACE)
    if value is None:
        return None

    if not match_us(raw[34], BIT_MARK):
        return None

    address = value & 0xFF
    command = (value >> 8) & 0xFF

    return DecodeResult(
        protocol="JVC",
        manufacturer="JVC",
        value=value,
        address=address,
        command=command,
        bits=NBITS,
        raw_count=len(raw),
    )


def decode_panasonic(raw: List[float]) -> Optional[DecodeResult]:
    """Decode Panasonic / Kaseikyo protocol (48-bit).

    Frame structure:
      Header : 3500 mark + 1750 space
      48 bits LSB-first : manufacturer_id(16) + addr(16) + cmd(16)
      Bit    : 432 mark + 1296 space (1) / 432 space (0)
      Stop   : 432 mark

    Known manufacturer IDs are mapped to brand names.
    """
    HDR_MARK   = 3500
    HDR_SPACE  = 1750
    BIT_MARK   = 432
    ONE_SPACE  = 1296
    ZERO_SPACE = 432
    NBITS      = 48

    # header(2) + 48 bit-pairs(96) + stop(1) = 99
    if len(raw) < 99:
        return None
    if not match_us(raw[0], HDR_MARK):
        return None
    if not match_us(raw[1], HDR_SPACE):
        return None

    value = _decode_pdm_bits(raw, 2, NBITS, BIT_MARK, ONE_SPACE, ZERO_SPACE)
    if value is None:
        return None

    if not match_us(raw[98], BIT_MARK):
        return None

    manufacturer_id = (value >> 32) & 0xFFFF
    address         = (value >> 16) & 0xFFFF
    command         = value & 0xFFFF

    _MFR_MAP = {
        0x4004: "Panasonic",
        0x0000: "Denon",
        0x0301: "JVC (Kaseikyo)",
        0x0201: "Sharp (Kaseikyo)",
        0x7F7F: "Mitsubishi (Kaseikyo)",
    }
    manufacturer = _MFR_MAP.get(manufacturer_id,
                                f"Kaseikyo(0x{manufacturer_id:04X})")

    return DecodeResult(
        protocol="PANASONIC",
        manufacturer=manufacturer,
        value=value,
        address=address,
        command=command,
        bits=NBITS,
        raw_count=len(raw),
        extra={"manufacturer_id": manufacturer_id},
    )


def decode_sharp(raw: List[float]) -> Optional[DecodeResult]:
    """Decode Sharp protocol (15-bit, no header pulse).

    Frame structure (no header):
      15 bits : addr(5) + cmd(8) + ctrl(2)
      Bit     : 320 mark + 1000 space (1) / 680 space (0)
    """
    BIT_MARK   = 320
    ONE_SPACE  = 1000
    ZERO_SPACE = 680
    NBITS      = 15

    # 15 bit-pairs = 30 entries
    if len(raw) < 30:
        return None

    # Sharp has no header; the first mark must match BIT_MARK
    if not match_us(raw[0], BIT_MARK):
        return None

    value = _decode_pdm_bits(raw, 0, NBITS, BIT_MARK, ONE_SPACE, ZERO_SPACE)
    if value is None:
        return None

    address = value & 0x1F
    command = (value >> 5) & 0xFF

    return DecodeResult(
        protocol="SHARP",
        manufacturer="Sharp",
        value=value,
        address=address,
        command=command,
        bits=NBITS,
        raw_count=len(raw),
    )


def decode_rc5(raw: List[float]) -> Optional[DecodeResult]:
    """Decode Philips RC5 Manchester-encoded protocol (14-bit).

    Frame structure:
      14 bits MSB-first : S1(1) + S2(1) + Toggle + Addr(5) + Cmd(6)
      Each bit is biphase (Manchester) encoded with half-period T1 = 889 µs.
      Bit=1 : mark→space transition at midpoint (first half HIGH).
      Bit=0 : space→mark transition at midpoint (first half LOW).
    """
    RC5_T1   = 889
    RC5_BITS = 14

    if len(raw) < RC5_BITS:
        return None

    # All timings must be approximately T1 or 2*T1
    for t in raw:
        if not (match_us(t, RC5_T1) or match_us(t, 2 * RC5_T1)):
            return None

    # Expand each timing into half-period states
    # raw[0] is a mark, raw[1] a space, alternating
    half_periods: List[int] = []
    is_mark = True
    for t in raw:
        count = 2 if match_us(t, 2 * RC5_T1) else 1
        state = 1 if is_mark else 0
        half_periods.extend([state] * count)
        is_mark = not is_mark

    if len(half_periods) < RC5_BITS * 2:
        return None

    # Extract bits: each bit uses two consecutive half-periods
    #   [1,0] → bit=1 (first half HIGH → falling transition)
    #   [0,1] → bit=0 (first half LOW  → rising  transition)
    bits: List[int] = []
    for i in range(RC5_BITS):
        first  = half_periods[i * 2]
        second = half_periods[i * 2 + 1]
        if first == 1 and second == 0:
            bits.append(1)
        elif first == 0 and second == 1:
            bits.append(0)
        else:
            return None

    # Validate start bits (must both be 1)
    if bits[0] != 1 or bits[1] != 1:
        return None

    toggle  = bits[2]
    address = sum(bits[3 + i] << (4 - i) for i in range(5))
    command = sum(bits[8 + i] << (5 - i) for i in range(6))
    value   = sum(bits[i] << (RC5_BITS - 1 - i) for i in range(RC5_BITS))

    return DecodeResult(
        protocol="RC5",
        manufacturer="Philips RC5",
        value=value,
        address=address,
        command=command,
        bits=RC5_BITS,
        raw_count=len(raw),
        extra={"toggle": toggle},
    )


# ---------------------------------------------------------------------------
# Top-level decode dispatcher
# ---------------------------------------------------------------------------

_DECODERS = [
    decode_nec,
    decode_samsung,
    decode_sony,
    decode_lg,
    decode_jvc,
    decode_panasonic,
    decode_sharp,
    decode_rc5,
]


def decode_ir_raw(raw_us: List[float]) -> Optional[DecodeResult]:
    """Try every supported decoder and return the first successful result.

    Parameters
    ----------
    raw_us:
        List of alternating mark/space durations **in microseconds**.
        ``raw_us[0]`` is the first mark, ``raw_us[1]`` the first space, etc.

    Returns
    -------
    A :class:`DecodeResult` on success, or ``None`` if no protocol matched.
    """
    for decoder in _DECODERS:
        result = decoder(raw_us)
        if result is not None:
            return result
    return None
