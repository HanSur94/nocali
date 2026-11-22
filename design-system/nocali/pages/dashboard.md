# Dashboard Page Overrides (NoCali main page)

> **PROJECT:** NoCali · **Page:** `app/index.html` (the only page)
> Rules here **override** `design-system/nocali/MASTER.md`. Everything not listed follows the Master.

---

## Why these overrides

The Master was generated from the query "3D printer monitoring utility dashboard local tool" and picked
**Minimalism & Swiss Style** + **Real-Time Monitoring** with the *Smart Home/IoT Dashboard* palette
(dark tech + status green) and the *Dashboard Data* font pairing (Fira Code / Fira Sans).
Its page pattern ("Real-Time / Operations Landing") is for marketing pages; NoCali is a local tool,
so the layout below replaces it.

## Layout

- App shell: sticky top bar (logo, connection status pill, language switch, settings button) + content.
- Max width **1120px**, 16px side gutter on phones, 24px from 768px.
- **≥ 900px:** two columns, `minmax(0, 1.35fr) minmax(0, 1fr)`.
  - Left: **Status** (hero: state, job, big percent, progress, layer/remaining/finish), **alerts**, **camera**.
  - Right: **Send file** (drop zone, primary action), **Temperatures**, **Printer**, **AMS**.
- **< 900px:** one column in this order: Status → alerts → Send file → Camera → Temperatures → Printer → AMS.
- No horizontal scroll at 375px; AMS slots 4-up from 480px, 2-up below.

## Typography (deviation: no web fonts)

NoCali runs offline on localhost and makes no third-party requests, so Google Fonts are **not** loaded.
Fira is used when installed, with system fallbacks:

- Body: `"Fira Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`, 16px / 1.5
- Data (numbers, codes, temperatures): `"Fira Code", ui-monospace, "SF Mono", Menlo, Consolas, monospace`,
  `font-variant-numeric: tabular-nums`
- Scale: 12 (labels, uppercase +0.06em) · 14 (secondary) · 16 (body) · 20 (card values) · 28 (state) · 40 (percent)

## Colors (adds a light theme; dark = Master)

| Token | Dark (Master) | Light | Use |
|---|---|---|---|
| `--bg` | `#0F172A` | `#F8FAFC` | page |
| `--card` | `#1B2336` | `#FFFFFF` | cards |
| `--muted` | `#272F42` | `#F1F5F9` | tiles, chips, track |
| `--fg` | `#F8FAFC` | `#0F172A` | text |
| `--fg-muted` | `#94A3B8` | `#475569` | secondary text (≥ 4.5:1 on card in both) |
| `--border` | `#334155` | `#E2E8F0` | dividers (decorative) |
| `--border-strong` | `#64748B` | `#7C8BA1` | control outlines (≥ 3:1) |
| `--accent` | `#22C55E` | `#15803D` | live, progress, primary button, active slot |
| `--on-accent` | `#0F172A` | `#FFFFFF` | text on accent |
| `--warn` | `#F59E0B` | `#B45309` | warnings, vibration-calibration stage |
| `--danger` | `#F87171` | `#DC2626` | errors, fatal HMS |
| `--info` | `#38BDF8` | `#0369A1` | info HMS |
| `--ring` | `#FFFFFF` | `#0F172A` | focus ring |

Status is never shown by color alone: every state also has text (and an icon for alerts).

## Components

- **Cards:** 12px radius, 1px `--border`, 20px padding (16px < 480px), card title = 12px uppercase label + 20px icon.
- **Buttons:** min-height 44px, 8px radius; primary = accent fill; secondary = outline `--border-strong`;
  hover 150ms colour change only (no layout shift); `:focus-visible` 2px `--ring` outline, 2px offset.
- **Icons:** one family, inline SVG outline, 24px viewBox, stroke 1.75, drawn at 20px (16px inline).
  Decorative icons next to text get `aria-hidden="true"`. No emoji.
- **Connection pill:** dot + text ("live", "connecting…"); the dot pulses only while live
  (2s, disabled under `prefers-reduced-motion`). One `role="status"` region for connection; alerts use `role="alert"`.
- **Alerts:** full-width, left border 4px in severity colour + icon + level label + text + code + wiki link.
- **Progress:** 8px track, accent fill, `role="progressbar"` with `aria-valuenow`.
- **Camera:** click-to-play (never autoplays), 16:9 reserved box (no layout shift), stop button.
- **Settings:** native `<dialog>` (Esc closes, focus returns to the button), visible labels, error under the form.
- **Drop zone:** dashed `--border-strong`, keyboard operable (Enter/Space), drag-over state = accent border + tint.

## Motion

- Tokens: `--dur-fast: 150ms` (hover/press), `--dur-base: 250ms` (dialog, reveal), `--dur-slow: 600ms` (progress width).
- `prefers-reduced-motion: reduce` → all durations 0, no pulse.
- No GSAP: the Master's scroll-reveal snippet is not used (the page is a live tool, content must be visible immediately).
