# logic2-infrared

A [Logic 2](https://www.saleae.com/downloads/) High Level Analyzer (HLA) extension that decodes IR remote-control signals captured on a digital channel, using **IRremoteESP8266**-compatible protocol detection.

## Supported protocols

| Protocol | Manufacturer examples | Bits |
|---|---|---|
| NEC / NEC Extended | Generic remotes, Apple | 32 |
| Samsung | Samsung TVs & appliances | 32 / 36 |
| Sony SIRC 12/15/20 | Sony AV equipment | 12 / 15 / 20 |
| LG | LG TVs & appliances | 28 |
| JVC | JVC AV equipment | 16 |
| Panasonic / Kaseikyo | Panasonic, Denon, Mitsubishi, Sharp (Kaseikyo) | 48 |
| Sharp | Sharp TVs | 15 |
| RC5 | Philips, many set-top boxes | 14 |

## Installation

1. Open **Logic 2**.
2. Go to **Extensions** (the puzzle-piece icon) → **Load Existing Extension…**
3. Select this directory.

The extension will appear as **"IR Remote Decoder"** in the High Level Analyzers list.

## Usage

1. Capture an IR signal on a digital channel.
2. Add the **IR Remote Decoder** HLA on top of that channel:
   - Open the **Analyzers** panel (the waveform icon in the left sidebar).
   - Click **+ Add Analyzer**.
   - Scroll to (or search for) **IR Remote Decoder** under *High Level Analyzers*.
   - In the dialog that appears, set **Input Analyzer** to the digital channel that carries your IR signal, then click **Save**.
3. Configure the two settings:
   - **gap_threshold_ms** – silence duration (in ms) that marks the end of one IR frame (default: 10 ms).
   - **active_low** – set to **Yes** for standard TSOP/IR demodulator receivers (output is active-LOW, idle = HIGH); set to **No** for active-HIGH signals.
4. Decoded frames appear in the Logic 2 timeline showing `manufacturer [protocol] addr=0xADDR cmd=0xCMD val=0xVAL`.

### Output frame types

| Frame type | Description |
|---|---|
| `ir_frame` | Successfully decoded IR frame with manufacturer, protocol, address, command, and full value. |
| `ir_raw` | IR burst that could not be matched to any known protocol; shows raw pulse count and first 20 durations (µs). |
| `ir_error` | Burst too short to attempt decoding. |

## Architecture

```
logic2-infrared/
├── extension.json         # Logic 2 extension manifest
├── HighLevelAnalyzer.py   # Logic 2 HLA — accumulates digital frames,
│                          #   detects inter-frame gaps, calls ir_decoder
├── ir_decoder.py          # Standalone IR protocol decoder (no Logic 2
│                          #   dependency); can be used independently
└── tests/
    └── test_ir_decoder.py # pytest unit tests for all protocol decoders
```

`ir_decoder.py` is a self-contained module with no external dependencies.
It mirrors the IRremoteESP8266 timing constants and decoding logic.

## Running the tests

```bash
pip install pytest
pytest tests/
```

## How it works

The HLA accumulates the duration of each digital frame (mark or space) into a list.
When a space longer than `gap_threshold_ms` is detected, the accumulated list is
passed to `decode_ir_raw()` in `ir_decoder.py`, which tries each protocol decoder
in priority order and returns the first match as a `DecodeResult` containing:

- `protocol` — short tag (e.g. `"NEC"`)
- `manufacturer` — human-readable name (e.g. `"NEC"`, `"Samsung"`, `"Panasonic"`)
- `address`, `command` — decoded fields per-protocol
- `value` — full raw decoded integer
- `bits` — number of data bits
