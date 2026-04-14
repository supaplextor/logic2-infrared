"""
HighLevelAnalyzer.py — Logic 2 High Level Analyzer for IR remote decoding.

Place this analyzer on top of a digital channel that carries an IR signal.
The analyzer accumulates pulse/space durations, detects inter-frame gaps,
then calls the IRremoteESP8266-compatible decoder in ir_decoder.py to
identify the protocol, manufacturer and data payload.

Settings
--------
gap_threshold_ms : float (1–200 ms, default 10 ms)
    Minimum silence duration that marks the end of an IR frame.
active_low : "Yes" | "No" (default "Yes")
    Set to "Yes" when the IR receiver output is active-LOW (e.g. TSOP
    series demodulators).  The idle state is then HIGH; marks are LOW.
    Set to "No" for active-HIGH signals (marks are HIGH).
"""

from __future__ import annotations

from typing import List, Optional

from saleae.analyzers import (
    AnalyzerFrame,
    ChoicesSetting,
    HighLevelAnalyzer,
    NumberSetting,
)

from ir_decoder import DecodeResult, decode_ir_raw

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_GAP_MS = 10.0   # inter-frame gap threshold in milliseconds
_MIN_PULSES     = 5      # discard bursts shorter than this


# ---------------------------------------------------------------------------
# High Level Analyzer
# ---------------------------------------------------------------------------

class Hla(HighLevelAnalyzer):
    """IR Remote Decoder for Logic 2.

    Accumulates digital pulse/space durations from a digital channel and
    decodes them using IRremoteESP8266-compatible protocol detection.

    Output frame types
    ------------------
    ``ir_frame``
        A successfully decoded IR frame.  Data keys:
          - ``manufacturer`` (str)  — e.g. ``"NEC"``, ``"Samsung"``
          - ``protocol``     (str)  — short protocol tag, e.g. ``"NEC"``
          - ``address``      (str)  — hex string, e.g. ``"00FF"``
          - ``command``      (str)  — hex string, e.g. ``"807F"``
          - ``value``        (str)  — full hex value
          - ``bits``         (int)  — number of data bits

    ``ir_raw``
        An IR burst that could not be decoded.  Data keys:
          - ``count`` (int) — number of pulses collected
          - ``raw``   (str) — first 20 durations (µs), truncated

    ``ir_error``
        A burst that was too short to attempt decoding.  Data keys:
          - ``reason`` (str)
    """

    # ---- settings ----------------------------------------------------------
    gap_threshold_ms = NumberSetting(min_count=1, max_count=200)
    active_low       = ChoicesSetting(choices=("Yes", "No"))

    # ---- output frame formats ----------------------------------------------
    result_types = {
        "ir_frame": {
            "format": (
                "{{data.manufacturer}} [{{data.protocol}}] "
                "addr=0x{{data.address}} cmd=0x{{data.command}} "
                "val=0x{{data.value}}"
            ),
        },
        "ir_raw": {
            "format": "RAW ({{data.count}} pulses): {{data.raw}}",
        },
        "ir_error": {
            "format": "IR error: {{data.reason}}",
        },
    }

    # ---- lifecycle ---------------------------------------------------------

    def __init__(self) -> None:
        self._pulses: List[float] = []   # durations in µs (alternating mark/space)
        self._frame_start = None         # GraphTime of first pulse in current burst
        self._frame_end   = None         # GraphTime of last pulse in current burst
        self._gap_us: Optional[float]   = None
        self._is_active_low: Optional[bool] = None

    # ---- helpers -----------------------------------------------------------

    def _init_settings(self) -> None:
        """Lazy initialisation from settings (called once on first frame)."""
        try:
            gap_ms = float(self.gap_threshold_ms)
        except (TypeError, ValueError):
            gap_ms = _DEFAULT_GAP_MS
        # Defensive clamp in case settings are bypassed in tests or future refactors.
        self._gap_us = max(gap_ms, 1.0) * 1_000.0   # ms → µs

        self._is_active_low = (str(self.active_low).strip() != "No")

    def _get_is_mark(self, frame: AnalyzerFrame) -> Optional[bool]:
        """Return True if *frame* is an IR mark (carrier active), else False.

        Tries several key names used by different Logic 2 analyzer layers.
        Returns None when the state cannot be determined from frame data.
        """
        for key in ("input_state", "state", "value", "data"):
            if key in frame.data:
                raw_state = frame.data[key]
                if isinstance(raw_state, (int, bool)):
                    logic_level = bool(raw_state)
                    # For active-LOW: mark = LOW (logic_level False)
                    # For active-HIGH: mark = HIGH (logic_level True)
                    if self._is_active_low:
                        return not logic_level
                    return logic_level
        return None

    def _flush(self, end_time) -> AnalyzerFrame:
        """Decode accumulated pulses and return an AnalyzerFrame."""
        raw = list(self._pulses)
        start_time = self._frame_start

        if len(raw) < _MIN_PULSES:
            return AnalyzerFrame(
                "ir_error", start_time, end_time,
                {"reason": f"too few pulses ({len(raw)})"},
            )

        result: Optional[DecodeResult] = decode_ir_raw(raw)

        if result is not None:
            hex_w = (result.bits + 3) // 4
            return AnalyzerFrame(
                "ir_frame", start_time, end_time,
                {
                    "manufacturer": result.manufacturer,
                    "protocol":     result.protocol,
                    "address":      f"{result.address:04X}",
                    "command":      f"{result.command:04X}",
                    "value":        f"{result.value:0{hex_w}X}",
                    "bits":         result.bits,
                },
            )

        raw_preview = [round(d) for d in raw[:20]]
        return AnalyzerFrame(
            "ir_raw", start_time, end_time,
            {
                "count": len(raw),
                "raw": str(raw_preview) + ("..." if len(raw) > 20 else ""),
            },
        )

    def _reset(self) -> None:
        self._pulses = []
        self._frame_start = None
        self._frame_end   = None

    # ---- main decode loop --------------------------------------------------

    def decode(self, frame: AnalyzerFrame):  # type: ignore[override]
        """Process one digital frame and return a decoded IR frame or None."""
        if self._gap_us is None:
            self._init_settings()

        duration_us = float(frame.end_time - frame.start_time) * 1_000_000.0
        is_mark = self._get_is_mark(frame)

        # When we cannot determine the signal state from frame data, fall back
        # to parity: even-indexed pulses (0, 2, …) are treated as marks,
        # odd-indexed as spaces.  This heuristic assumes the capture starts on
        # a mark (i.e. the very first digital frame is an IR burst mark).  If
        # the capture begins mid-space the parity will be inverted and
        # decoding will fail; users should ensure their capture window starts
        # just before the IR transmission.
        if is_mark is None:
            is_mark = (len(self._pulses) % 2 == 0)

        # ---- inter-frame gap detection ------------------------------------
        # A long space (duration > gap threshold) signals end of IR frame.
        if (not is_mark) and (duration_us > self._gap_us):
            if len(self._pulses) >= _MIN_PULSES:
                result_frame = self._flush(frame.start_time)
                self._reset()
                return result_frame
            # Too few pulses — discard and start fresh
            self._reset()
            return None

        # ---- accumulate pulse --------------------------------------------
        if is_mark and self._frame_start is None:
            # First mark of a new IR burst
            self._frame_start = frame.start_time

        if self._frame_start is not None:
            self._pulses.append(duration_us)
            self._frame_end = frame.end_time

        return None
