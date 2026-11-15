# PS3 dynamic theme formats

How a PS3 dynamic theme is put together, from the store package down to the 3D scene and its script.
Field layouts come from retail themes and from Sony's theme toolchain (`raf_compiler`, `raf_geom`,
`raf_anim`, `raf_script`, built on PLAYSTATION Edge 0.4.3).

All multi-byte values are **big-endian** unless noted.

| Layer | Tool |
|---|---|
| PKG → files | `tools/ps3_pkg_extract.py` |
| `.edat` → `.p3t` (needs the `.rap`) | `tools/ps3_edat_decrypt.py` |
| `.p3t` / RAF → tree XML + files, GIM/GTF → PNG | `tools/p3t_unpack.py` |
| Edge geometry, skins, animation → glTF | `tools/raf_to_gltf.py` |
| Skeleton and inverse bind | `tools/es01.py` |
| Animation (EA03) | `tools/ea03.py`, see [ea03-format.md](ea03-format.md) |
| Scene script (VSMX) | `tools/vsmx.py`, `viewer/vsmx_vm.js` |

---

## 1. PKG (retail, type 0x0001)

| Off | Type | Meaning |
|---|---|---|
| 0x00 | char[4] | `\x7FPKG` |
| 0x04 | u16 | revision (0x8000 = retail) |
| 0x06 | u16 | type (1 = PS3) |
| 0x14 | u32 | item count |
| 0x20 | u64 | data offset |
| 0x28 | u64 | data size |
| 0x30 | char[0x30] | content ID |
| 0x70 | u8[16] | AES-CTR initial counter |

The data area is AES-128-CTR with key `2E7B71D7C9C9A14EA3221F188828B8F8`; the counter is header[0x70..0x80]
plus the 16-byte block index. The item table at the start of the data area holds 32-byte entries
`u32 nameOff, u32 nameSize, u64 dataOff, u64 dataSize, u32 flags, u32 pad` (offsets relative to the data area;
`flags & 0xFF == 4` marks a directory).

A theme package holds `PS3LOGO.DAT` (a PNG) and the theme itself as `<content id>.p3t.edat`.

## 2. EDAT (NPD)

NPD header: `NPD\0`, u32 version, u32 license (2 = local, needs a RAP), u32 type, content ID at 0x10,
digest at 0x40 (used as the CBC IV), dev hash at 0x60. EDAT header at 0x80: u32 flags, u32 blockSize, u64 fileSize.

- klicensee = AES-ECB-decrypt(RAP key, rap), then 5 rounds of the RAP p-box / E1 / E2 scramble.
- Block key i = AES-ECB(klicensee, devHash[0:12] ‖ be32(i)); with flags & 0x8 the key is additionally
  AES-CBC-decrypted with EDAT_KEY0 and a zero IV. Block data = AES-CBC(key, IV = digest).
- With flags & 0x20 each block is preceded by 0x20 bytes of metadata: block i starts at 0x100 + i·(blockSize + 0x20).

## 3. P3T (`P3TF`) and RAF (`RAFO`) containers

Both use the same resource-tree layout (as in Sony's RCO files):

| Off | Field |
|---|---|
| 0x00 | magic `P3TF` / `RAFO` |
| 0x04 | u32 version (0x110) |
| 0x08 / 0x0C | tree offset / size |
| 0x10 / 0x14 | ID table offset / size (`[u32 nodeOffset][name\0]` entries) |
| 0x18 / 0x1C | string table offset / size (tag names, attribute names and string values) |
| 0x20 / 0x24 | int array table |
| 0x28 / 0x2C | float array table |
| 0x30 / 0x34 | file data offset / size |

Tree node: 7 × u32 `name, attrCount, parent, prev, next, firstChild, lastChild` (offsets relative to the tree,
0xFFFFFFFF = none), followed by `attrCount` 16-byte attributes `u32 name, u32 type, u32 v1, u32 v2`:

| Type | Meaning |
|---|---|
| 1 | int `v1` |
| 2 | float `v1` |
| 3 | string: `v1` offset, `v2` length in the string table |
| 4 | int[]: `v1` index, `v2` count in the int table |
| 5 | float[]: `v1` index, `v2` count in the float table |
| 6 | file: `v1` offset, `v2` size in the data area |
| 7 | ID definition (`v1` = ID-table entry offset, name at +4) |
| 8 | ID reference (same encoding as 7) |

Embedded files are zlib streams (`78 DA`). The dynamic background is `_RAF` + u32 size + zlib, which inflates
to a `RAFO` container. P3T images (icons, pointers, previews) are GIM (`.GIM1.00\0PSP`, big-endian, RGBA8888);
RAF textures are GTF (CellGcmTexture header, DXT1/3/5 or A8R8G8B8).

## 4. RAF scene tree

```
raf
├─ scene: camera, light × N, script, actor × N
├─ model-table:    model → geometry, inv-bind, skeleton, animation
├─ material-table: material(effect) → _texture(texref) × N
├─ texture-table:  texture(filters, wrap, fileref)
└─ file-table:     file(src, id)
```

Actor attributes, with the compiler's defaults:

| Attribute | Default | Meaning |
|---|---|---|
| `model`, `material` | — | references into the tables |
| `position` | 0, 0, 0 | |
| `rotation` | 0, 0, 0 | radians (Euler) |
| `scale` | 1, 1, 1 | |
| `color` | 1, 1, 1, 1 | RGBA tint; alpha drives script fades |
| `uv_scale` | 1, −1 | texture coordinate scale (V flipped) |
| `uv_offset` | 0, 0 | texture scroll |
| `anim_weight` | 1, 0, 0, 0, 0, 0, 0, 0 | blend weight per animation slot |
| `anim_speed` | 1 × 8 | playback speed per slot |
| `anim_time` | 0 × 8 | start offset per slot |
| `zsort` | empty | draw ordering |

Rotations are always stored in radians: the scene XML that `raf_compiler` reads may declare `angle_unit`,
and anything other than `radian` is multiplied by π/180 at compile time. The viewer draws transparent
objects in actor order.

## 5. Edge geometry (`.edge`)

```
u32 segmentCount, u32 0x10, u32 ?, u32 totalSize
segmentCount × 0x80-byte segment records (EdgeGeomPpuConfigInfo) from 0x10
```

| Record offset | Field |
|---|---|
| +0x00 | EdgeGeomSpuConfigInfo: u8 flagsAndUniformTableCount, u8 commandBufferHoleSize, u8 inputVertexFormatId, u8 secondaryInputVertexFormatId, u8 outputVertexFormatId, u8 vertexDeltaFormatId, u8 indexesFlavor<<4 \| skinningFlavor, u8 skinningMatrixFormat, u16 numVertexes, u16 numIndexes, u32 indexesOffset |
| +0x10 | u32 indexes offset, u16 indexesSizes[2] |
| +0x18 | u32 spuVertexes[2] (stream 0 = positions, f32 × 3; stream 1) |
| +0x20 | u16 spuVertexesSizes[6] |
| +0x2C | u32 rsxOnlyVertexes offset (stream 2, e.g. UVs), +0x30 u32 size |
| +0x34 | u16 skinMatricesByteOffsets[2] |
| +0x38 | u16 skinMatricesSizes[2] |
| +0x3C | u16 skinIndexesAndWeightsSizes[2] |
| +0x40 | u32 skinIndexesAndWeights offset |
| +0x44 / +0x48 | u32 ioBufferSize, u32 scratchSize |
| +0x6C | u32 descriptor offsets: stream 1 input, output, stream 2 input |

**Vertex stream descriptor:** u8 count, u8 stride, then 8-byte declarations from +8:
`u8 offset, u8 codec, u8 components, u8 semantic`.

- Codecs: 1 int16 normalized, 2 f32, 3 half, 4 u8 / 255, 5 int16, 6 packed 11:11:10 signed
  (x bits 0–10, y 11–21, z 22–31), 7 u8, 9 spherical normal.
- Semantics: 1 position, 2 normal, 3 tangent, 5 UV, 9 weights.

### Compressed indices (indexesFlavor 3)

From `libedgegeom_compressindexes.cpp`:

```
u16 numExplicit, u16 bias, u16 flagBytes, u8 deltaBits, u8 0
flags[flagBytes]       1 bit per new-vertex slot, MSB first: 0 = next sequential vertex, 1 = explicit index
codes[ceil(tris / 4)]  2 bits per triangle, MSB first
explicit[...]          numExplicit × deltaBits bits, MSB first
```

With `prev = (p0, p1, p2)` the previous triangle:

| Code | Triangle |
|---|---|
| 0 | (p0, p2, new) |
| 1 | (p2, p1, new) |
| 2 | (p1, p0, new) |
| 3 | (new, new, new) |

Each "new" consumes one flag bit. Explicit values: `d = v − bias`; `x[i] = d` for i < 8, otherwise
`x[i] = x[i − 8] + d`; `x` is the absolute vertex index. Vertices are numbered in first-use order.

### Skinning

Per vertex: 8 bytes = 4 × (u8 weight, u8 local index); the weights sum to 255. Local indices walk matrix
range 0 and then range 1, where range k covers joints `skinMatricesByteOffsets[k] / 48` up to
`(skinMatricesByteOffsets[k] + skinMatricesSizes[k]) / 48` (one 3×4 float matrix = 48 bytes).

## 6. Skeleton (`ES01`, `.skel`)

| Off | Field |
|---|---|
| 0x00 | `ES01` |
| 0x0C | u16 numJoints |
| 0x14 | self-relative → base pose: numJoints × (quat xyzw f32, trans xyzw f32, scale xyzw f32), joint-local |
| 0x18 | self-relative → u16 parent per joint (0xFFFF = root) |
| 0x1C | self-relative → u32 joint name hash per joint |
| 0x30 | SIMD hierarchy processing table |

Self-relative means: target = address of the field + its value. Rigid models have a 0-joint skeleton and no
inverse-bind file.

## 7. Inverse bind (`.edge.invbind`)

numJoints × 3×4 f32, row-major (`[r00 r01 r02 tx] [..] [..]`). Skin matrix = `jointWorld · invBind`.
The bind pose is separate from the skeleton's base pose.

## 8. Animation (`EA03`, `.anim`)

Edge Animation, bit-packed with per-channel quantisation. Full layout in [ea03-format.md](ea03-format.md).
`raf_to_gltf.py` exports one glTF animation per animated actor (per-frame samples of joint-local TRS) and stores
the actor's `anim_speed` in `animation.extras.speed`, which the viewer applies as the clip's time scale.
Almost every object in a dynamic theme is a rigged model driven this way, props included.

## 9. Scene script (VSMX)

Compiled JavaScript (the same VSMX format used in RCO files), little-endian.

Header: `VSMX`, u32 version (0x20000), then u32 codeOff, codeLen, textOff, textLen, textCount, propOff, propLen,
propCount, namesOff, namesLen, namesCount. Code is 8-byte instructions `u32 opword, u32 operand`; the opcode is
`opword & 0xFF` and `opword >> 8` is an extra field. Tables are NUL-separated strings (text and props UTF-16LE,
names 8-bit); operands are entry indices.

Opcodes 0x00–0x4E (names from `raf_script`):

```
NOP ASSGN ADD SUB MUL DIV MOD TO_NUMBER CSIGN NOT INC DEC POST_INC POST_DEC CMPEQ CMPNEQ CMPSEQ CMPSNEQ
CMPLT CMPLE CMPGE CMPGT INSTANCEOF IN TYPEOF BIT_AND BIT_XOR BIT_OR BIT_NOT LSHIFT S_RSHIFT U_RSHIFT COPY
SWAP REMOVE PUSH_UNDEFINED PUSH_NULL PUSH_BOOL PUSH_INT PUSH_FLOAT PUSH_STRING PUSH_OBJECT PUSH_FUNC
PUSH_ARRAY PUSH_THIS PUSH_LOCAL PUSH_GLOBAL GETATTR GETATTR_KEEPOBJ SETATTR DELATTR APPEND_ATTR GETITEM
GETITEM_KEEPOBJ SETITEM DELITEM APPEND_ITEM JUMP JUMPT JUMPF CALL_FUNC CALL_METHOD CALL_CONSTRUCTOR RET
THROW TRYBLK_IN TRYBLK_OUT CATCH_FINALLYBLK_IN CATCH_FINALLYBLK_OUT HALT DEBUG_FILE DEBUG_LINE
GETITEM_KEEPOBJNAME PUSH_VECTOR GET_VECTOR_ELEMENT GET_VECTOR_ELEMENT_KEEPVECTOR ASSGN_VECTOR_ELEMENT
SETATTR_VECTOR_ELEMENT SETITEM_VECTOR_ELEMENT
```

Stack-machine semantics (JavaScript rules):

- `PUSH_GLOBAL`, `PUSH_LOCAL`, `GETATTR` and `GETITEM` push references; `ASSGN`, `INC` and `POST_INC` write through them.
- `PUSH_FUNC`: operand = entry pc; extra = `nlocals << 16 | argc`. Local 0 is the arguments array; arguments
  are locals 1..argc.
- `CALL_FUNC n`: `[f, a1..an]`. `CALL_METHOD n`: `[obj, f, a1..an]` (f comes from `GETATTR_KEEPOBJ`).
  `CALL_CONSTRUCTOR n`: `[ctor, a1..an]`.
- `SETATTR`: `[obj, value]`. `SETITEM`: `[obj, key, value]`. `APPEND_ATTR` builds object literals in place.
- `TRYBLK_IN h`: on an exception, restore the stack, push the exception and continue at h + 1 (h is a
  `CATCH_FINALLYBLK_IN`). Reaching `CATCH_FINALLYBLK_IN t` normally jumps to t.
- `PUSH_VECTOR n` packs n numbers into a vector. `SETATTR_VECTOR_ELEMENT`: operand = property, extra =
  component (e.g. `color.w`).

### Host API

| Object | Members |
|---|---|
| `new Actor(id)` | `enable`, `color` (RGBA), `scale`, `position`, `uv_offset`, `uv_scale`, `setColor()`, `setScale()`, `getAnimTime()`, `setAnimTime()` |
| `new Light(id)`, `new Camera(id)` | `position`, `direction`, `color` / `up` |
| `System` | `timer[]` (assign an `IntervalTimer`), `printPerf()` |
| `new IntervalTimer(seconds, fn)` | calls `fn` every `seconds` |
| `new Date()` | `hours`, `minutes`, `seconds` |
| `writeln(...)` | debug log |

The top-level code defines everything and ends with `startScene()`, which installs
`System.timer[0] = new IntervalTimer(1 / fps, processFrame)`.

### Theme state machines

Theme scripts drive their scenes with a shared state-machine library (`statetable.js`, `runtime_states.js`):

- `gblObjects[i]` is a group of actors plus `stateInformation[]`. Each state machine has `currentState`,
  `framesThisScene` and a `transitionTable` indexed by state, whose entries hold `next`, `scenarioLen`
  (seconds, or `'rand'` for a random length between `min` and `max`, default 3–10), `setupMethod`,
  `runMethod` and `setupVariables` (typically `actorFrom`, `actorTo`, `cmNonLinear`).
- Each frame, progress = `framesThisScene / (fps × scenarioLen)`; at 1 the machine moves to `next`.
  Cross-fades ease the `actorFrom` / `actorTo` alphas through `color.w`.
- `transitionToState(renderSettings, stateInfo, group, forcedNext)` performs a transition; `forcedNext`
  jumps directly to a given state. The viewer's States panel uses it.
- `STATE_NAMES` holds readable state names.
