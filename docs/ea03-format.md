# PS3 Edge Animation "EA03" (.anim) format

Source: decompiled encoder in `raf_anim.exe` (`libedgeanim_animation.cpp` writer `sub_48b7c0`,
`libedgeanim_bitpacking.cpp` `sub_4996e0`/`sub_4998b0`/`sub_4999e0`/`sub_499a30`/`sub_497d50`,
smallest-3 `sub_47f9d0`). Decoder: `tools/ea03.py`.


All multi-byte values are big-endian (the writer byte-swaps when the PS3 target flag is set).
"rel" = self-relative offset: target = address of the field + value; 0 means absent.
The writer places named labels (`StartHeader`, `EndHeader`, ...) and fixes up offsets/sizes
between them (`sub_4993c0` = u16 label difference, `sub_4993f0` = u32 label difference; with an
empty base label it is self-relative).

## 1. Header (0x00..0x53)

| off | type | field | notes |
|----|----|----|----|
| 0x00 | char[4] | magic `EA03` | (0x45413033) |
| 0x04 | f32 | duration (s) | endTime-startTime = (numFrames-1)/fps |
| 0x08 | f32 | sampleFrequency (fps), e.g. 24 |
| 0x0C | u16 | headerSize = EndHeader-StartHeader (whole header incl. tables, specs, DMA list, FrameSetInfo, aligned 16; must be <= 0x2000) |
| 0x0E | u16 | numJoints |
| 0x10 | u16 | numFrames |
| 0x12 | u16 | numFrameSets |
| 0x14 | u16 | evalBufferSize (0x2000) |
| 0x16 | u16 | numConstR | channels with exactly 1 key |
| 0x18 | u16 | numConstT |
| 0x1A | u16 | numConstS |
| 0x1C | u16 | numConstUser |
| 0x1E | u16 | numAnimR | channels with >1 key |
| 0x20 | u16 | numAnimT |
| 0x22 | u16 | numAnimS |
| 0x24 | u16 | numAnimUser |
| 0x26 | u16 | flags: bit0 R, bit1 T, bit2 S, bit3 User use BITPACKED compression (mode 2). If clear: R = SMALLEST_3 (mode 1; mode 0 is illegal for R), T/S/U = NONE (raw f32) |
| 0x28 | u32 | weights size (WeightsEnd-WeightsStart) |
| 0x2C | u32 | 0 |
| 0x30 | u32 | weights offset relative to file start (0 = none) |
| 0x34 | u32 rel | FrameSetDmaEntries |
| 0x38 | u32 rel | FrameSetInfo |
| 0x3C | u32 rel | ConstantRData |
| 0x40 | u32 rel | ConstantTData |
| 0x44 | u32 rel | ConstantSData |
| 0x48 | u32 rel | ConstantUserData |
| 0x4C | u32 rel | PackingSpecs |
| 0x50 | u32 rel | CustomData (0 = none) |

A joint/channel with no keys at all is not listed anywhere (e.g. only 1 of 3 joints animated);
such channels keep the skeleton's base pose.

## 2. Channel index tables (align 16 -> 0x60)

u16 joint indices (user channels: user-channel index), in order:
constR (padded to a multiple of **8**), constT, constS, constUser, animR, animT, animS, animUser
(each padded to a multiple of **4**). Padding entries = numJoints (numUserChannels for the user
lists). An empty list takes 0 bytes. Within a list joints are ascending.

## 3. Constant data
Each block is one bit stream flushed to a byte boundary (or raw floats):
- ConstantRData (align 16 if smallest-3): per constR joint: bitpacked quat with the *global R spec*, or 48-bit smallest-3.
- ConstantTData / ConstantSData (align 16 if NONE): bitpacked vec3 with the global T/S spec, or 3 x f32.
- ConstantUserData (align 4): one value with the global User spec's first component, or f32.

## 4. PackingSpecs (align 4; written only if any type is bitpacked)
For each type R,T,S,User whose flag bit is set: one u32 **global spec** (used for the constant
channels of that type) followed by one u32 spec per animated channel (in animX table order).
Then align 16.

Spec word (sub_4999e0):
```
bits 31..22 comp0, 21..12 comp1, 11..2 comp2, 1..0 extra
each 10-bit comp = [9] signBits(0/1) | [8..5] expBits(0..15) | [4..0] mantBits(0..31)
```
`extra` = for R: index (0..3 in x,y,z,w order) of the quaternion component that is NOT stored;
the three stored components are the remaining ones in x,y,z,w order. For T/S/U it is
unused (written as 3). Component width on the stream = sign+exp+mant bits; 0 bits => value 0.

### Bitpacked component dequantisation (sub_4998b0)
`v` = the (s+e+m)-bit field read MSB-first.
- **e > 0 (mini-float):** `exp=((v>>m)&(2^e-1)) + bias[e]`, bias[e]=128-2^(e-1) (table 0x50945c:
  128,127,126,124,120,112,96,64,0,-128,...); `mant=(v & (2^m-1))` shifted to 23 bits;
  exp<0 -> 0.0, exp>=256 -> clamp (0xFF,0x7FFFFF); `sign=(v>>(m+e)) & (2^s-1)`;
  float bits = sign<<31 | exp<<23 | mant.
- **e = 0, m > 0 (fixed point):** if s: sign-extend v as an (m+1)-bit two's-complement
  (test bit 1<<m, OR ~(2^m-1)); value = v / (2^m - 1). Unsigned: v/(2^m-1).
- e = m = 0: value 0.
- Rotation: the dropped component = +sqrt(max(0, 1 - sum of squares of the other three)); the
  encoder negates the quaternion so that component is >= 0.

The encoder chooses specs per channel by searching the smallest bit counts meeting the
tolerance (sub_49a390). Example: 0x00000841 = drop y, x/z 0 bits, w = 1 sign + 16 mant
bits (rotation about Y only).

### Smallest-3 quaternion (48 bits, mode 1)
Largest-|c| component index i is dropped (quaternion negated if q[i] < 0). 64-bit value:
`comp_a<<32 | comp_b<<17 | comp_c<<2 | i` (a,b,c = remaining comps in x,y,z,w order), low 48
bits written MSB-first (bit 47 always 0). Each comp = ftol(c*23169.77 + 16383.5) (scale float at
0x53c418 = 16383.5*sqrt2), so each component fits 15 bits. Retail themes use the bitpacked mode.

## 5. FrameSetDmaEntries (align 16)
numFrameSets x 8 bytes: `u16 0, u16 dmaSize, u32 frameSetOffset` (offset from file start).
dmaSize = (FrameSetInitialPoseEnd of set i+1) - (FrameSetStart of set i), rounded up to 16
(the runtime DMAs set i plus the next set's initial pose); 0 for the last set.

## 6. FrameSetInfo (align 4)
numFrameSets x `u16 baseFrame, u16 numIntraFrames`. Set i covers frames
baseFrame .. baseFrame+numIntraFrames; next baseFrame = base+numIntra+1. Last set has 0 intra
frames (just the final pose). e.g. (0,128),(129,128),(258,40),(299,0) for 300 frames.
Then align 16 = EndHeader. Weights (if present) follow: numJoints x u8 per joint (+ align 4) then
numUserChannels x u8, value = round(w*255), 0 or 255 at the extremes; omitted when all weights
are >= 0.998.

## 7. Frame set (each aligned 16)
```
u16 size[8]: InitialR, InitialT, InitialS, InitialU, IntraR, IntraT, IntraS, IntraU
             (byte length of each block incl. its trailing alignment pad)
InitialRData   one bit stream: value of every animR channel at baseFrame (channel spec / smallest-3)
InitialTData   vec3 per animT channel (bitpacked or 3xf32)
InitialSData
(align 4) InitialUData
IntraBitStream (not in size table): for each animR channel, then animT, animS, animU:
               numIntraFrames bits (MSB-first); bit k set => a key exists at frame baseFrame+1+k.
               length = ceil(numAnimChannels*numIntraFrames/8)
IntraRData     channel-major: for each animR channel, its keys in frame order
IntraTData, IntraSData, IntraUData
(align 16) FrameSetEnd
```
Each Initial/Intra block is a separate bit stream flushed to a byte. Bit-writer (sub_497d50):
values appended MSB-first into a 64-bit accumulator. Block positions are obtained by summing
the sizes (the bit-stream size is computed). Bit k of a channel's intra bitmap is the key at
frame baseFrame + 1 + k (so baseFrame + numIntraFrames + 1 is the next set's base).

## 8. Evaluating a pose at time t
frame = t*fps. For each animated channel the key list is: initial pose of every set (at its
baseFrame) + intra keys. Between keys interpolate linearly (T/S/U lerp; R nlerp with
hemisphere fix).
Constant channels: the single value. Channels not present: bind pose from the ES01 skeleton.
Frames past the last key hold the last key.
