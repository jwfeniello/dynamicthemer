"""Decoder for Sony PS3 Edge Animation "EA03" (.anim) files (big-endian, as found
in PS3 dynamic themes / RAF scenes).  See docs/ea03-format.md for the spec.

decode_anim(data) -> dict(duration, fps, num_frames, joints={joint: {"r": [(t, (x,y,z,w))...],
                                                                    "t": [(t, (x,y,z))...],
                                                                    "s": [(t, (x,y,z))...]}},
                          user={channel: [(t, v)...]})
Every channel present in the file is sampled at every frame (t = frame / fps).
Joints/channels absent from the file keep the skeleton's bind pose.

CLI:  python ea03.py file.anim [more files...]   -> prints a summary
"""
import math
import struct
import sys

COMP_NONE, COMP_SMALLEST3, COMP_BITPACKED = 0, 1, 2


class BitReader:
    """MSB-first bit reader (matches the encoder's 64-bit accumulator writer)."""

    def __init__(self, data, pos=0):
        self.data = data
        self.bit = pos * 8

    def read(self, n):
        v = 0
        for _ in range(n):
            byte = self.data[self.bit >> 3]
            v = (v << 1) | ((byte >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return v


def _mask(n):
    return (1 << n) - 1


def _exp_bias(e):
    # table at 0x50945c: 128,127,126,124,120,112,96,64,0,-128,-384,...
    return 128 if e == 0 else 128 - (1 << (e - 1))


def parse_spec(u32):
    """Packing spec word -> (extra, [(signBits, expBits, mantBits) x3])."""
    comps = []
    for shift in (22, 12, 2):
        f = (u32 >> shift) & 0x3FF
        comps.append(((f >> 9) & 1, (f >> 5) & 0xF, f & 0x1F))
    return u32 & 3, comps


def comp_bits(c):
    return c[0] + c[1] + c[2]


def dequant(c, v):
    """sub_4998b0: one bit-packed component -> float."""
    s, e, m = c
    if e:
        exp = ((v >> m) & _mask(e)) + _exp_bias(e)
        mant = v & _mask(m)
        mant = (mant << (23 - m)) if m <= 23 else (mant >> (m - 23))
        if exp < 0:
            return 0.0
        if exp >= 256:
            exp, mant = 0xFF, 0x7FFFFF
        sign = (v >> (m + e)) & _mask(s)
        bits = (((sign << 8) | exp) << 23 | mant) & 0xFFFFFFFF
        return struct.unpack("<f", struct.pack("<I", bits))[0]
    if m == 0:
        return 0.0
    if s and (v & (1 << m)):
        v -= 1 << (m + 1)  # sign-extend (m+1)-bit two's complement
    return v / float(_mask(m))


def read_vec3_packed(br, comps):
    return tuple(dequant(c, br.read(comp_bits(c))) for c in comps)


def read_quat_packed(br, spec):
    drop, comps = spec
    vals = [dequant(c, br.read(comp_bits(c))) for c in comps]
    q = [0.0] * 4
    k = 0
    ss = 0.0
    for i in range(4):
        if i == drop:
            continue
        q[i] = vals[k]
        ss += vals[k] * vals[k]
        k += 1
    q[drop] = math.sqrt(max(0.0, 1.0 - ss))
    return tuple(q)


SM3_SCALE = 23169.767  # float at 0x53c418 (0x46b50389) = 16383.5*sqrt(2)
SM3_BIAS = 16383.5     # centres each 15-bit component


def read_quat_smallest3(br):
    v = br.read(48)
    idx = v & 3
    vals = [(((v >> sh) & 0x7FFF) - SM3_BIAS) / SM3_SCALE for sh in (32, 17, 2)]
    q = [0.0] * 4
    k = 0
    ss = 0.0
    for i in range(4):
        if i == idx:
            continue
        q[i] = vals[k]
        ss += vals[k] ** 2
        k += 1
    q[idx] = math.sqrt(max(0.0, 1.0 - ss))
    return tuple(q)


def _norm(q):
    l = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / l for c in q)


def _lerp(a, b, f):
    return tuple(x + (y - x) * f for x, y in zip(a, b))


def _qlerp(a, b, f):
    if sum(x * y for x, y in zip(a, b)) < 0:
        b = tuple(-x for x in b)
    return _norm(_lerp(a, b, f))


def _sample(keys, nframes, is_quat):
    """keys: sorted list of (frame, value) -> value per frame (linear / nlerp)."""
    out = []
    k = 0
    for f in range(nframes):
        while k + 1 < len(keys) and keys[k + 1][0] <= f:
            k += 1
        f0, v0 = keys[k]
        if f <= f0 or k + 1 >= len(keys):
            out.append(v0)
            continue
        f1, v1 = keys[k + 1]
        a = (f - f0) / float(f1 - f0)
        out.append(_qlerp(v0, v1, a) if is_quat else _lerp(v0, v1, a))
    return out


def decode_anim(data, raw=False):
    be = ">"
    u16 = lambda o: struct.unpack_from(">H", data, o)[0]
    u32 = lambda o: struct.unpack_from(">I", data, o)[0]
    f32 = lambda o: struct.unpack_from(">f", data, o)[0]

    if data[:4] != b"EA03":
        raise ValueError("not an EA03 animation")
    h = {}
    h["duration"] = f32(4)
    h["fps"] = f32(8)
    h["header_size"] = u16(0x0C)
    nj = h["num_joints"] = u16(0x0E)
    nf = h["num_frames"] = u16(0x10)
    nsets = h["num_frame_sets"] = u16(0x12)
    h["eval_buffer_size"] = u16(0x14)
    names = ["constR", "constT", "constS", "constU", "animR", "animT", "animS", "animU"]
    cnt = {n: u16(0x16 + 2 * i) for i, n in enumerate(names)}
    h["counts"] = cnt
    flags = u16(0x26)
    mode = [COMP_BITPACKED if flags & (1 << i) else None for i in range(4)]
    h["flags"] = flags

    def rel(o):
        v = u32(o)
        return o + v if v else None

    h["weights_size"] = u32(0x28)
    h["weights_offset"] = u32(0x30) or None  # relative to file start
    dma_off = rel(0x34)
    info_off = rel(0x38)
    const_off = [rel(0x3C), rel(0x40), rel(0x44), rel(0x48)]
    spec_off = rel(0x4C)
    h["custom_data"] = rel(0x50)

    # rotation/non-rotation mode when flag bit clear: R -> smallest3, T/S/U -> raw float
    if mode[0] is None:
        mode[0] = COMP_SMALLEST3
    for i in (1, 2, 3):
        if mode[i] is None:
            mode[i] = COMP_NONE
    h["modes"] = mode

    # channel index tables
    pos = 0x60
    tables = {}
    for i, n in enumerate(names):
        c = cnt[n]
        tables[n] = [u16(pos + 2 * k) for k in range(c)]
        al = 8 if i == 0 else 4
        padded = (c + al - 1) // al * al
        pos += 2 * padded
    h["tables"] = tables

    # packing specs: per type (if bitpacked): global spec, then one per animated channel
    specs = {}
    if spec_off is not None:
        p = spec_off
        for i, (cn, an) in enumerate((("constR", "animR"), ("constT", "animT"),
                                      ("constS", "animS"), ("constU", "animU"))):
            if mode[i] != COMP_BITPACKED:
                continue
            specs[cn] = parse_spec(u32(p)); p += 4
            specs[an] = []
            for _ in range(cnt[an]):
                specs[an].append(parse_spec(u32(p))); p += 4

    # constants
    consts = {"r": {}, "t": {}, "s": {}, "u": {}}
    for i, (key, cn) in enumerate((("r", "constR"), ("t", "constT"), ("s", "constS"), ("u", "constU"))):
        if not cnt[cn]:
            continue
        o = const_off[i]
        br = BitReader(data, o)
        for j in tables[cn]:
            if mode[i] == COMP_BITPACKED:
                sp = specs[cn]
                if key == "r":
                    v = read_quat_packed(br, sp)
                elif key == "u":
                    v = dequant(sp[1][0], br.read(comp_bits(sp[1][0])))
                else:
                    v = read_vec3_packed(br, sp[1])
            elif mode[i] == COMP_SMALLEST3:
                v = read_quat_smallest3(br)
            else:
                if key == "u":
                    v = f32(o); o += 4
                else:
                    v = struct.unpack_from(">3f", data, o); o += 12
            consts[key][j] = v

    # frame sets
    sets = []
    for k in range(nsets):
        base, nintra = u16(info_off + 4 * k), u16(info_off + 4 * k + 2)
        fs_off = u32(dma_off + 8 * k + 4)
        sets.append((base, nintra, fs_off, u16(dma_off + 8 * k + 2)))
    h["frame_sets"] = sets

    anim_keys = {"r": {j: [] for j in tables["animR"]}, "t": {j: [] for j in tables["animT"]},
                 "s": {j: [] for j in tables["animS"]}, "u": {j: [] for j in tables["animU"]}}
    kinds = (("r", "animR", 0), ("t", "animT", 1), ("s", "animS", 2), ("u", "animU", 3))

    def read_val(br, key, i, ci, o_holder):
        if mode[i] == COMP_BITPACKED:
            sp = specs[kinds[i][1]][ci]
            if key == "r":
                return read_quat_packed(br, sp)
            if key == "u":
                return dequant(sp[1][0], br.read(comp_bits(sp[1][0])))
            return read_vec3_packed(br, sp[1])
        if mode[i] == COMP_SMALLEST3:
            return read_quat_smallest3(br)
        o = o_holder[0]
        if key == "u":
            o_holder[0] += 4
            return f32(o)
        o_holder[0] += 12
        return struct.unpack_from(">3f", data, o)

    for base, nintra, off, _ in sets:
        sizes = [u16(off + 2 * k) for k in range(8)]
        p = off + 16
        sect = []
        for s_ in sizes[:4]:
            sect.append(p); p += s_
        nanim = sum(cnt[n] for n in ("animR", "animT", "animS", "animU"))
        bs_off = p
        p += (nanim * nintra + 7) // 8
        for s_ in sizes[4:]:
            sect.append(p); p += s_
        # initial pose
        for key, an, i in kinds:
            br = BitReader(data, sect[i]); oh = [sect[i]]
            for ci, j in enumerate(tables[an]):
                anim_keys[key][j].append((base, read_val(br, key, i, ci, oh)))
        # intra-frame key bits (channel-major: R chans, T, S, U; one bit per intra frame)
        bbr = BitReader(data, bs_off)
        bits = {}
        for key, an, i in kinds:
            for j in tables[an]:
                bits[(key, j)] = [bbr.read(1) for _ in range(nintra)]
        for key, an, i in kinds:
            br = BitReader(data, sect[4 + i]); oh = [sect[4 + i]]
            for ci, j in enumerate(tables[an]):
                for fi, b in enumerate(bits[(key, j)]):
                    if b:
                        anim_keys[key][j].append((base + 1 + fi, read_val(br, key, i, ci, oh)))

    fps = h["fps"]
    joints = {}
    user = {}
    for key in ("r", "t", "s"):
        for j, v in consts[key].items():
            joints.setdefault(j, {})[key] = [(f / fps, v) for f in range(nf)]
        for j, keys in anim_keys[key].items():
            keys.sort(key=lambda kv: kv[0])
            vals = _sample(keys, nf, key == "r")
            joints.setdefault(j, {})[key] = [(f / fps, v) for f, v in enumerate(vals)]
    for j, v in consts["u"].items():
        user[j] = [(f / fps, v) for f in range(nf)]
    for j, keys in anim_keys["u"].items():
        keys.sort(key=lambda kv: kv[0])
        vals = _sample([(f, (v,)) for f, v in keys], nf, False)
        user[j] = [(f / fps, v[0]) for f, v in enumerate(vals)]

    out = dict(duration=h["duration"], fps=fps, num_frames=nf, joints=joints, user=user)
    if raw:
        out["header"] = h
        out["specs"] = specs
        out["keys"] = anim_keys
    return out


def read_skeleton(data):
    """Minimal ES01 reader: base pose per joint (quat xyzw, trans xyz, scale xyz), parents."""
    if data[:4] != b"ES01":
        raise ValueError("not an ES01 skeleton")
    nj = struct.unpack_from(">H", data, 0x0C)[0]
    bp = 0x14 + struct.unpack_from(">I", data, 0x14)[0]
    pp = 0x18 + struct.unpack_from(">I", data, 0x18)[0]
    joints = []
    for i in range(nj):
        v = struct.unpack_from(">12f", data, bp + 0x30 * i)
        par = struct.unpack_from(">H", data, pp + 2 * i)[0]
        joints.append(dict(r=v[0:4], t=v[4:7], s=v[8:11], parent=par))
    return joints


def main(argv):
    for path in argv:
        with open(path, "rb") as f:
            d = decode_anim(f.read(), raw=True)
        h = d["header"]
        print("%s: %.3fs @ %.3f fps, %d frames, %d joints, %d frame sets, flags=0x%X" % (
            path, d["duration"], d["fps"], d["num_frames"], h["num_joints"], h["num_frame_sets"], h["flags"]))
        print("  counts:", " ".join("%s=%d" % kv for kv in h["counts"].items()))
        for j in sorted(d["joints"]):
            ch = d["joints"][j]
            parts = []
            for k in ("r", "t", "s"):
                if k in ch:
                    a, b = ch[k][0][1], ch[k][-1][1]
                    parts.append("%s %s->%s" % (k, tuple(round(x, 3) for x in a), tuple(round(x, 3) for x in b)))
            print("  joint %d: %s" % (j, "; ".join(parts)))
        for j in sorted(d["user"]):
            print("  user %d: %.3f -> %.3f" % (j, d["user"][j][0][1], d["user"][j][-1][1]))


if __name__ == "__main__":
    main(sys.argv[1:])
