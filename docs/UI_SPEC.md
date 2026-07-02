# Pathfinder — UI Architecture Specification
> Design-agnostic. No colors, no fonts, no visual style decisions.

## 0. Stack Decision (supersedes the Win32/Direct2D plan below)

**The HUD is built with Tauri**, not raw Win32 + Direct2D: a thin Rust shell
window (transparent, click-through, always-on-top, DPI-aware — all first-class
Tauri window APIs) hosting the actual panels/widgets as HTML/CSS/JS rendered
by WebView2 (already present on Windows 10/11 — no bundled Chromium).

Why: panels, fades, slides, and blur become CSS (`backdrop-filter`,
`transition`) instead of hand-rolled Direct2D draw calls — much faster to
iterate on visually. It also removes any GPL exposure (see below) and keeps
the artifact small.

The **sections below (1–10) describe the conceptual model** — window
invariants, the four panels, widget taxonomy, input bindings, animation,
DPI/multi-monitor handling, and the data-flow contract with the agent. That
model still holds; only the *implementation substrate* changes:

| Concept below | Win32/D2D (original) | Tauri (current) |
|---|---|---|
| Overlay window, Z-order, click-through | Win32 window styles | Tauri `WebviewWindowBuilder` (`.transparent()`, `.always_on_top()`, `.skip_taskbar()`, `.decorations(false)`) + per-element `-webkit-app-region`/JS↔Rust cursor-passthrough calls |
| Draw loop / dirty panels | `BeginDraw`/`EndDraw` | Browser paint pipeline; React/vanilla-JS re-renders only changed panel state |
| Widgets (Text/Bar/Shape/Image/Line/Button) | Custom `Widget` subclasses | HTML elements + CSS (a `<div>` + CSS is the ShapeWidget; a `<canvas>`/SVG is the LineWidget) |
| Fade / slide animation | `WM_TIMER` + manual alpha lerp | CSS `transition`/`animation` |
| Blur behind panel | `BLURMODE_REGION` | CSS `backdrop-filter: blur()` |
| DPI scaling | `WM_DPICHANGED`, manual rescale | Handled by WebView2; use CSS logical px + `devicePixelRatio` only if needed |
| Data push from agent | `PanelDataStore` → `WM_APP_DATA_UPDATE` → `Widget.Update()` | Rust shell polls/subscribes to the Python `ipc.py` endpoints (`/panels`, `/notifications`) and emits a Tauri event to the JS frontend, which re-renders |

**License note:** the sections below were originally derived by reading
Rainmeter source (`third_party/rainmeter`, GPL-2.0) for architectural patterns.
None of that code is used — Tauri/WebView2/CSS replace it entirely. The
Rainmeter submodule is kept only as a design reference and is not compiled
into, linked with, or copied into Pathfinder.

---

## 1. Window Model

### 1.1 Overlay Window
The HUD lives in a single Win32 window with the following invariants (sourced from `Skin.h`):

| Property | Value | Notes |
|----------|-------|-------|
| Z-Position | `ZPOSITION_ONTOPMOST` | Always above all other windows |
| Style | `WS_POPUP` | No titlebar, no frame |
| Extended style | `WS_EX_LAYERED \| WS_EX_TOOLWINDOW \| WS_EX_NOACTIVATE` | Transparent, hidden from taskbar, never steals focus |
| Click-through | Configurable per-region | Non-panel regions pass clicks through to the OS |
| Blur | `BLURMODE_REGION` | Blur only behind active panel areas, not the full screen |
| DPI | Per-monitor aware (`WM_DPICHANGED`) | Scale panels per monitor, not global |

### 1.2 Z-Position Enum (from Rainmeter)
```
ZPOSITION_ONDESKTOP  = -2   // Below desktop icons — not used in Pathfinder
ZPOSITION_ONBOTTOM   = -1   // Above desktop, below normal windows — not used
ZPOSITION_NORMAL     =  0   // Normal window stacking — not used
ZPOSITION_ONTOP      =  1   // Above normal, below always-on-top
ZPOSITION_ONTOPMOST  =  2   // Always on top — Pathfinder default
```

### 1.3 Visibility & Transitions
Sourced from `HIDEMODE` in `Skin.h`:
- `HIDEMODE_NONE` — always visible
- `HIDEMODE_FADEIN` — fade in on trigger
- `HIDEMODE_FADEOUT` — fade out when dismissed

Pathfinder uses **FADEIN/FADEOUT** for panel show/hide toggles.  
Fade duration is configurable (default: 200ms). Alpha range: 0–255.

---

## 2. Rendering Pipeline

### 2.1 Technology Stack
Sourced from `Common/Gfx/Canvas.h`:

```
Win32 HWND
  └── DXGI Swap Chain (IDXGISwapChain1)
        └── D3D11 Device + Context
              └── ID2D1DeviceContext  (main render target)
                    ├── IDWriteFactory1  (text)
                    └── IWICImagingFactory  (images)
```

Hardware-accelerated by default. Software fallback if device is lost.

### 2.2 Draw Loop
Each frame follows Rainmeter's `BeginDraw` / `EndDraw` pattern:

```
1. BeginDraw()
2. Clear(transparent)
3. For each visible Panel (back to front):
     a. PushOpacityLayer(panel.alpha)
     b. For each Widget in panel:
          Widget.Draw(canvas)
     c. PopLayer()
4. EndDraw()  →  Present swap chain
```

Only dirty panels are redrawn. A panel is marked dirty when its data changes (agent push) or its visibility toggles.

### 2.3 Coordinate System
- All layout values are **logical pixels** (device-independent).
- `LogicalToPhysical(value)` = `value * dpiScale * zoomScale`
- Sourced from `Skin.h`: separate `m_DpiScale` and `m_ZoomScale` — composited into `m_EffectiveScale`.
- All hit tests operate in **physical** coordinates; converted to logical before widget dispatch.

---

## 3. Panel System

### 3.1 Panel = Named Skin Region
A Panel is Pathfinder's equivalent of a Rainmeter skin — an independent, positioned, toggleable region that owns a set of Widgets.

```cpp
struct Panel {
    std::wstring  id;           // "recent_cases", "updates", "chronology", "whats_next"
    SkinPosition  x, y;        // Anchor position on screen
    int           w, h;        // Logical size
    bool          visible;
    int           alpha;        // 0–255
    HIDEMODE      hideMode;     // FADEIN / FADEOUT
    BLURMODE      blurMode;     // NONE / REGION
    std::vector<Widget*> widgets;
};
```

### 3.2 The Four Panels

| ID | Purpose | Default Position |
|----|---------|-----------------|
| `recent_cases` | Highlight strip of recently touched cases | Top-right |
| `updates` | Major changes + chargesheet reminders | Below recent_cases |
| `chronology` | Timeline of a selected case | Left edge |
| `whats_next` | Smart scheduler / ranked next actions | Bottom-right |

Panels are **independently toggled** — toggling one does not affect others.  
The toggle control strip (panel switches) is a fixed, non-fading element always at `ZPOSITION_ONTOPMOST`.

### 3.3 Panel Positioning
Sourced from `SkinPosition.h` pattern — positions expressed as:
- Absolute pixel offset from a screen edge
- Or relative to another panel (`POSITION_RELATIVE_TL`, `POSITION_RELATIVE_BR`)

Panels **snap to screen edges** if within 10px (SnapEdges behaviour from `Skin.h`).

---

## 4. Widget System

### 4.1 Base Widget (≈ Rainmeter `Meter`)
All drawable elements inherit from a common base:

```cpp
class Widget {
public:
    virtual void Initialize() = 0;
    virtual bool Update()     = 0;   // Pull new data from agent
    virtual bool Draw(Gfx::Canvas& canvas) = 0;
    virtual bool HitTest(int x, int y);
    void Show(); void Hide();
    bool IsHidden();

    int x, y, w, h;
    D2D1_RECT_F padding;
    bool antiAlias;
    METER_POSITION relativeX, relativeY;  // ABSOLUTE / RELATIVE_TL / RELATIVE_BR
};
```

Widgets are positioned **relative to their parent Panel**, not the screen.

### 4.2 Widget Types

Directly derived from Rainmeter's meter taxonomy:

#### TextWidget (≈ `MeterString`)
Renders a single text string.

| Property | Options |
|----------|---------|
| Style | NORMAL, BOLD, ITALIC, BOLDITALIC |
| Effect | NONE, SHADOW, BORDER |
| Case | NONE, UPPER, LOWER, PROPER |
| Clip | OFF, ON, AUTO (truncate with ellipsis) |
| Alignment | 9 positions (LEFT/CENTER/RIGHT × TOP/CENTER/BOTTOM) |
| Angle | Rotation in radians |

Used for: case IDs, timestamps, labels, status text, section headers.

#### BarWidget (≈ `MeterBar`)
A filled progress/fill bar.

| Property | Options |
|----------|---------|
| Orientation | HORIZONTAL, VERTICAL |
| Value | 0.0–1.0 (normalized) |
| Flip | bool — grow from opposite end |
| Border | pixel inset |

Used for: chargesheet deadline countdown, case progress.

#### ShapeWidget (≈ `MeterShape`)
Direct2D geometry: Rectangle, RoundedRectangle, Ellipse, Line, Path.  
Supports: fill, stroke, gradient brushes, transforms (translate/scale/rotate/skew), combined shapes.

Used for: panel backgrounds, dividers, status indicators, timeline nodes.

#### ImageWidget (≈ `MeterImage`)
Renders a bitmap. Supports scale, tile, mask.  
Used for: icons, badges.

#### LineWidget (≈ `MeterLine`)
Plots a value history as a polyline.  
Used for: sparklines in Recent Cases (e.g., case activity over time).

#### ButtonWidget (≈ `MeterButton`)
Clickable region with distinct up/down/hover states.  
Used for: panel toggle controls, dismiss buttons on reminders.

### 4.3 Widget Alignment Reference
```
ALIGN_LEFT          ALIGN_CENTER          ALIGN_RIGHT
ALIGN_LEFTCENTER    ALIGN_CENTERCENTER    ALIGN_RIGHTCENTER
ALIGN_LEFTBOTTOM    ALIGN_CENTERBOTTOM    ALIGN_RIGHTBOTTOM
```
Text origin anchors to the specified corner/edge of the widget bounding box.

### 4.4 Container Widgets
Any Widget can act as a **container** — it clips and hosts child widgets in its own render texture.  
Sourced from `Meter.h`: `m_ContainerItems`, `m_ContainerContentTexture`, `m_ContainerTexture`.

Used for: scrollable case lists inside a panel (clip children to panel bounds).

---

## 5. Input Handling

### 5.1 Mouse Action Map
Sourced from `Mouse.h`. Each widget can bind any of these:

```
LMB_UP / LMB_DOWN / LMB_DBLCLK
MMB_UP / MMB_DOWN / MMB_DBLCLK
RMB_UP / RMB_DOWN / RMB_DBLCLK
MW_UP  / MW_DOWN  / MW_LEFT   / MW_RIGHT   (scroll)
MOUSE_OVER / MOUSE_LEAVE
```

### 5.2 Pathfinder Input Bindings

| Gesture | Target | Action |
|---------|--------|--------|
| `LMB_UP` on toggle button | Panel | Show/hide panel (fade) |
| `LMB_UP` on case row | Recent Cases widget | Expand chronology panel to that case |
| `RMB_UP` on panel | Panel | Context menu (pin / move / dismiss) |
| `MW_DOWN / MW_UP` | Case list | Scroll |
| `MOUSE_OVER` on reminder | Updates widget | Expand detail |
| `MOUSE_LEAVE` on reminder | Updates widget | Collapse detail |

### 5.3 Click-Through Regions
Non-widget areas of the HUD window use `WS_EX_TRANSPARENT` behaviour — clicks fall through to whatever is behind the window.  
Only widget bounding boxes that pass `HitTest()` consume input.

---

## 6. Animation

### 6.1 Fade
Panel show/hide: linear alpha interpolation over `fadeDuration` ms.  
Implemented via `WM_TIMER` tick updating `m_TransparencyValue` (Rainmeter pattern from `Skin.h`).

### 6.2 Transitions
Widget-level transitions (e.g., new case arriving in Recent Cases):  
- Slide-in from right: translate X from `+w` → `0` over `transitionDuration` ms
- Value change in BarWidget: lerp value, not snap

`HasActiveTransition()` returns true while any widget is mid-transition — keeps the draw loop running at full rate until settled.

---

## 7. Data Flow to UI

Widgets do **not** pull data themselves. The agent pushes updates:

```
AgentLoop detects change
  → updates PanelDataStore (thread-safe)
    → posts WM_APP_DATA_UPDATE to HUD window
      → HUD WndProc calls Panel.Refresh()
        → each Widget.Update() reads from PanelDataStore
          → Widget marks itself dirty
            → next draw loop renders the change
```

This keeps the rendering thread free of blocking I/O.

---

## 8. DPI & Multi-Monitor

- Window registers for `WM_DPICHANGED` (per-monitor DPI v2).
- On DPI change: recompute `m_DpiScale`, call `Canvas.SetDpiScale()`, re-layout all panels.
- Panel positions stored in logical pixels; re-projected to physical on each DPI change.
- If a panel moves to a different monitor: re-query that monitor's DPI, re-scale.

---

## 9. What Pathfinder Does NOT Borrow from Rainmeter

| Rainmeter feature | Reason excluded |
|-------------------|-----------------|
| INI skin config parser | Pathfinder panels are code-defined, not user-scriptable |
| Lua scripting engine | No scripting layer needed |
| Plugin DLL interface | MCPs replace plugins |
| Measure system (CPU/net/etc.) | AgentLoop replaces measures |
| Multi-skin manager | Single HUD, not a multi-skin desktop |
| Config dialogs / tray icon | Background agent, no user configuration UI |

---

## 10. File Map (Rainmeter source → Pathfinder use) — SUPERSEDED

This table is kept for history only. It described the original Win32/Direct2D
plan and listed several files as "Direct copy" — that would have pulled
GPL-2.0-licensed code into the Pathfinder binary. **Under the Tauri stack
(section 0), none of this applies: no Rainmeter code is copied, referenced,
compiled, or linked.** The `third_party/rainmeter` submodule may be removed
once the Tauri shell is in place; it serves no build purpose.

| Rainmeter concept | Pathfinder equivalent (Tauri) |
|---------------|----------------------|
| `Common/Gfx/Canvas.h/.cpp` (rendering pipeline) | Browser paint pipeline (WebView2) |
| `Common/Gfx/Shape.*` (geometry primitives) | CSS / SVG / `<canvas>` |
| `Common/Gfx/TextFormat*` (text formatting) | CSS text properties |
| `Library/Skin.h` (window flags, fade, DPI) | Tauri `WebviewWindowBuilder` + CSS transitions |
| `Library/Meter.h` / `MeterString.h` / `MeterBar.h` / `MeterShape.h` | HTML elements + CSS (see section 0 table) |
| `Library/Mouse.h` (MOUSEACTION enum) | Native DOM mouse events |
| `Library/SkinPosition.h` (panel anchor logic) | CSS positioning / flex/grid layout |
