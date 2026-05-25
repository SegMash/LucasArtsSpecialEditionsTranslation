# Reverse Engineering Workflow: Mirror X-Coordinate in Monkey2.exe

## Goal

Find the function that draws a glyph/letter at screen position `(x, y)` and
change it to draw at `(screen_width − x, y)`.  This enables Hebrew right-to-
left text rendering without changing the game's string/script logic.

## What we know about the binary

| Property       | Value                                |
|----------------|--------------------------------------|
| Architecture   | x86 (32-bit)                         |
| Image base     | `0x00400000`                         |
| Renderer       | Direct3D 9 + custom HLSL (sprite2d.fx) |
| Font system    | `SpriteFont` class inside `TwoDee::Text` |
| Key string VA  | `SpriteFont` @ `0x00530838`          |
| Key string VA  | `TwoDee::Text` @ `0x00530828`        |
| Key string VA  | `fonts/fonts.dir` @ `0x00530194`     |
| Code xref VA   | Both strings referenced at `~0x004DCA00` |
| Coordinate math| x87 FPU + SSE2 (`movss`/`movsd`)    |

---

## Phase 1 — Set up analysis tools

### Option A: Ghidra (recommended, free)

1. Download **Ghidra** from <https://ghidra-sre.org>
2. Install Java 17+ (required by Ghidra)
3. Launch Ghidra → New Project → Non-Shared → Import `Monkey2.exe`
   - Format: `Portable Executable (PE)`
   - Language: `x86 / 32-bit / little-endian`
4. Double-click the file → Run auto-analysis → accept defaults and click **Analyze**
5. Wait for analysis to finish (~1–3 min)

### Option B: x32dbg (dynamic analysis)

1. Download **x32dbg** from <https://x64dbg.com>
2. Extract — no install needed
3. Launch `x32dbg.exe` → File → Open → `Monkey2.exe`

---

## Phase 2 — Find the SpriteFont Draw method (Ghidra path)

### Step 1: Navigate to the `SpriteFont` string

- In Ghidra: **Search → For String** → search `SpriteFont`
- Double-click the result at address `0x00530838`
- In the **References** panel you'll see one reference from code: `0x004DCA0C`

### Step 2: Examine the referencing function

- Double-click the reference to jump to `0x004DCA0C`
- You are now in the class registration / constructor code for `TwoDee::Text`
- Look upward in the Listing for the function prologue (typically `push ebp; mov ebp, esp` or `sub esp, N`)

### Step 3: Find the Draw virtual method

The `SpriteFont` object has a C++ vtable.  Its `Draw` method is what actually
places glyphs on screen.  To find it:

1. In the Decompile window, look for how the `SpriteFont` pointer is obtained
   (it will be a field of `TwoDee::Text`)
2. Find calls through that pointer's vtable: `call [pSprite + offset]`
3. Alternatively, use **Search → For Instruction Patterns** and search for
   `FF 96 XX XX 00 00` or `FF 91 XX XX 00 00` (call through esi/ecx + offset)

### Step 4: Follow the call chain to a glyph placement

The draw chain typically looks like:

```
TwoDee::Text::Render(...)
  → SpriteFont::DrawText(str, x, y, color, ...)
    → SpriteFont::DrawChar(ch, x, y, color)   ← THIS IS THE TARGET
      → Sprite2D::Draw(texture, srcRect, dstX, dstY, ...)
        → D3D9 device DrawPrimitive / DrawPrimitiveUP
```

Look for:
- A function that receives a **character code** (usually in the range 0–255 or a
  glyph index) AND two coordinate values
- The function that multiplies/adds glyph widths to advance the x cursor

### Step 5: Identify where x is written to the vertex buffer

In the Decompile view, the glyph draw function will eventually build a rectangle
(quad) like:

```c
vertex[0].x = x;
vertex[1].x = x + glyph_width;
vertex[2].x = x;
vertex[3].x = x + glyph_width;
```

or with SSE:

```asm
movss xmm0, [x_param]
movss [vertex_x_left],  xmm0
addss xmm0, [glyph_w]
movss [vertex_x_right], xmm0
```

**The patch target is the first assignment** — where `x_param` is stored into
the vertex's x-coordinate.

---

## Phase 2 (alternative) — Find it dynamically with x32dbg

### Step 1: Set a breakpoint on D3D9 DrawPrimitive

The easiest approach is to break when D3D9 submits geometry.  D3D9 calls are
made through the COM vtable of `IDirect3DDevice9`.

In x32dbg:
1. Start the game, get to a scene that shows text
2. Plugins → ScyllaHide → Hook Options (disable anti-debug if needed)
3. In the **Symbols** tab: navigate to `d3d9.dll`
4. Use the **Memory Map** tab to find `d3d9.dll` base address
5. The vtable for `IDirect3DDevice9` is at a known offset inside d3d9.dll

Alternative: use **APIMonitor** (free) to log `DrawPrimitive` calls without
patching.

### Step 2: Break and trace back to glyph code

When a text character is being drawn:
1. Execution breaks inside d3d9.dll's `DrawPrimitive`
2. Look at the **Call Stack** panel — the frame just above `DrawPrimitive` is
   the Monkey2 code that submitted the draw
3. Go to that address in the Disassembly panel
4. Walk backwards to find where the vertex x-coordinate was computed

### Step 3: Inspect vertex buffer

Before `DrawPrimitive` is called, the game calls `SetStreamSource` to bind a
vertex buffer.  The buffer contains the x,y positions of each vertex of the
glyph quad.  Examine memory at the buffer address to confirm x values match
the on-screen glyph position.

---

## Phase 3 — Design the patch

### Case A: Integer x in a register (e.g., `eax` or `ecx`)

Suppose you find this at VA `0x004D1234`:
```asm
mov  [esp+8], eax        ; pass x as first coord arg
```

Replace with:
```asm
neg  eax                 ; eax = -x
add  eax, 1280           ; eax = 1280 - x   (7 bytes total)
mov  [esp+8], eax        ; (if you have room)
```

`neg eax` = `F7 D8` (2 bytes)  
`add eax, 0x00000500` = `05 00 05 00 00` (5 bytes)

Use `patch_mirror_x.py --build-bytes` to generate these.

### Case B: Float x in `xmm0` (SSE2)

Suppose the glyph x is in `xmm0` as a 32-bit float:
```asm
movss  [vert+0], xmm0    ; store x vertex
```

You need:
```asm
; xmm0 = screen_width_float - xmm0
; Place the float constant 1280.0 (= 0x44A00000) in a code cave
movss  xmm1, [cave_addr]  ; xmm1 = 1280.0
subss  xmm1, xmm0         ; xmm1 = 1280 - x
movaps xmm0, xmm1         ; xmm0 = result
```

### Case C: Code cave / JMP trampoline (when there is no in-place room)

If the target instruction is exactly 5 bytes (or you can NOP-pad to 5):
1. Find a code cave (run `patch_mirror_x.py --find-caves`)
2. Write your mirror-x logic into the cave
3. End the cave with `JMP` back to the instruction *after* the target
4. Replace the target with `CALL cave_addr` (5 bytes = `E8 rel32`)

`E8 rel32` where `rel32 = cave_va - (target_va + 5)`

---

## Phase 4 — Apply the patch

1. Edit `patch_mirror_x.py` — fill in `PATCHES[0]`:

```python
PATCHES = [
    dict(
        id="mirror_x_glyph",
        description="Mirror x: screen_width - x",
        va=0x004D1234,              # VA you found
        original=bytes.fromhex("89442408"),        # original bytes
        patched= bytes.fromhex("F7D80500050089442408"),  # patched bytes
        enabled=True,
    ),
]
```

2. Run:
```bash
python scripts/reverse-engineering/patch_mirror_x.py --verify
python scripts/reverse-engineering/patch_mirror_x.py --apply
```

3. Test the game.

4. To undo:
```bash
python scripts/reverse-engineering/patch_mirror_x.py --restore
```

---

## Phase 5 — Handle glyph advance direction

After mirroring the x position, the glyph *advance* (the cursor moving forward
after each character) also needs to go in the opposite direction.  Typically:

- Before: `x_cursor += glyph_advance_width`
- After:  `x_cursor -= glyph_advance_width`

This is a second patch, at the location where the cursor is incremented.  Use
the same workflow to find it.

---

## Quick reference: useful script commands

```bash
# Full PE analysis
python scripts/reverse-engineering/pe_analysis.py

# Show strings containing "font" or "draw"
python scripts/reverse-engineering/pe_analysis.py --strings --filter font,draw,text

# Show all code xrefs to strings matching "sprite"
python scripts/reverse-engineering/pe_analysis.py --xrefs sprite

# Find text rendering anchors + D3D9 patterns
python scripts/reverse-engineering/find_render_routines.py

# Also search full .text for D3D9 vtable calls
python scripts/reverse-engineering/find_render_routines.py --deep

# Disassemble 256 bytes at a VA
python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00

# Disassemble 512 bytes
python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00 --len 512

# Find all code that references a specific VA (e.g., a function address)
python scripts/reverse-engineering/disasm_region.py --va 0x004DCA00 --find-xrefs

# Find code caves (places to put new code)
python scripts/reverse-engineering/patch_mirror_x.py --find-caves

# Show mirror-x byte sequences
python scripts/reverse-engineering/patch_mirror_x.py --build-bytes

# Dump the patch target site before applying
python scripts/reverse-engineering/patch_mirror_x.py --dump-site 0x004D1234

# Apply patch (after filling in PATCHES in the script)
python scripts/reverse-engineering/patch_mirror_x.py --apply

# Restore original bytes
python scripts/reverse-engineering/patch_mirror_x.py --restore
```

---

## Addresses identified so far

| Symbol                        | VA           | Notes                                                  |
|-------------------------------|--------------|--------------------------------------------------------|
| `TwoDee::Text` string         | `0x00530828` | Debug/type name in .rdata                              |
| `SpriteFont` string           | `0x00530838` | Debug/type name in .rdata                              |
| `fonts/fonts.dir` string      | `0x00530194` | Font directory config path                             |
| `sprite2d.fx` shader path     | `0x00530448` | HLSL shader for all 2D rendering                       |
| TwoDee::Text ctor region      | `0x004DC9D0` | Class registration function start                      |
| sprite2d.fx loader            | `0x004D6069` | Renderer initialization                                |
| fontDirectory reader          | `0x004C63E3` | Font loading code                                      |
| **x-coord processor**         | `0x004D63C0` | Processes x, stores to `0x005CFDB4` (global x var)    |
| **x-coord subsd**             | `0x004D63FC` | `subsd xmm2, xmm0` — double-precision x arithmetic    |
| **x-coord store**             | `0x004D640C` | `movss [0x005CFDB4], xmm1` — stores raw x arg         |
| **y-coord store**             | `0x004D6404` | `movss [0x005CFDB8], xmm0` — stores processed coord   |
| Global x position             | `0x005CFDB4` | Runtime BSS global — current render x                 |
| Global y/delta                | `0x005CFDB8` | Runtime BSS global — render delta/y                   |
| x-global consumer             | `0x004D9126` | Pushes `&0x005CFDB4` to renderer/shader call          |
| y-global consumer             | `0x004D913F` | Pushes `&0x005CFDB8` to renderer/shader call          |
| Text drawing call chain       | `0x004DCBC0` | Large text rendering function (80-byte stack frame)    |
| **Patch target (TODO)**       | `???`        | Must confirm via Ghidra decompile or x32dbg watchpoint |

## Key insight: x-coord processor at `0x004D63C0`

The function at `0x004D63C0` takes a float x-coordinate as argument (`[esp+4]`)
and ends by:

```asm
0x004D640C: movss [0x005CFDB4], xmm1   ; xmm1 = raw x argument -> stored as global x
```

**The simplest patch** is to change this so it stores `screen_width - x` instead
of `x`.  Since the function uses SSE2, the in-place patch is:

```asm
; Replace the 8-byte store at 0x004D640C with a JMP into a code cave:
; Original: F3 0F 11 0D B4 FD 5C 00 (movss [0x005CFDB4], xmm1)
; Patch:    E9 XX XX XX XX 90 90 90  (jmp to_cave + 3× NOP)

; Code cave content:
  movss  xmm2, [float_const_1280]     ; F3 0F 10 15 [addr]  = 8 bytes
  subss  xmm2, xmm1                   ; F3 0F 5C D1          = 4 bytes
  movss  [0x005CFDB4], xmm2           ; F3 0F 11 15 B4FD5C00 = 8 bytes
  jmp    0x004D6414                   ; E9 rel32              = 5 bytes
  ; float constant 1280.0f = bytes: 00 00 A0 44
```

**However**: since there are no code caves (no INT3/NOP padding in `.text`), you
must add a new section using:
```bash
python scripts/reverse-engineering/patch_mirror_x.py --add-section
```
(implementation template is in the `add_cave_section()` function)

**Important caveat**: `0x004D63C0` is called via **vtable** (not direct CALL),
so it might be the virtual `Draw`/`SetPosition` method of `SpriteFont`.  Verify
in Ghidra before patching:

1. In Ghidra: navigate to `0x004D63C0`
2. Right-click → `References → Show References to this function`
3. If references are indirect (through vtable), they will show as data references
   pointing into a vtable array — find the vtable object and trace who uses it
