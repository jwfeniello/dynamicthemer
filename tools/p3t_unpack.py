"""Unpack a PS3 theme (.p3t, magic P3TF) into an XML tree + embedded files.
Dynamic-theme backgrounds (RAF containers, magic RAFO) use the same tree
layout and are unpacked recursively into <name>_raf/.
GIM images (RGBA8888) are also converted to PNG.
Usage: python -I p3t_unpack.py theme.p3t outdir"""
import os, struct, sys, zlib
from xml.sax.saxutils import quoteattr

# Header (all u32 BE, after 4-byte magic):
#  ver, tree_off, tree_sz, id_off, id_sz, str_off, str_sz,
#  intarr_off, intarr_sz, floatarr_off, floatarr_sz, data_off, data_sz
# Tree node: name, attr_count, parent, prev, next, first_child, last_child (7*u32)
# followed by attr_count * {name, type, v1, v2}.
# Attr types: 1 int, 2 float, 3 string(off,len), 4 int[] (count, off),
#             5 float[] (count, off), 6 file(off,len in data section), 7 id ref
#             8 ref to node? (shown raw)

def ext_of(blob):
    for magic, e in ((b"\x89PNG", "png"), (b"\xff\xd8", "jpg"), (b".GIM", "gim"), (b"MIG.", "gim"),
                     (b"RAFO", "raf"), (b"\x02\x01\x01", "gtf"), (b"<?xml", "xml"), (b"OMG.", "gmo"), (b"RIFF", "wav")):
        if blob.startswith(magic): return e
    return "bin"

def gim_to_png(blob, path):
    """Minimal GIM -> PNG for big-endian PS3 GIMs, RGBA8888 linear (format 3, order 0)."""
    from PIL import Image
    p = 0x10
    end = len(blob)
    while p < end:
        bid, _, bsize, nxt, doff = struct.unpack(">HHIII", blob[p:p + 16])
        if bid == 4:  # image block
            h = p + doff
            fmt, order, w, hgt = struct.unpack(">HHHH", blob[h + 4:h + 12])
            pix_off = struct.unpack(">I", blob[h + 0x1C:h + 0x20])[0]
            if fmt != 3 or order != 0:
                return f"unsupported GIM fmt={fmt} order={order}"
            px = blob[h + pix_off: h + pix_off + w * hgt * 4]
            Image.frombytes("RGBA", (w, hgt), px).save(path)
            return None
        p += doff if bid in (2, 3) else nxt
        if nxt == 0 and bid not in (2, 3): break
    return "no image block"

def _unswizzle(px, w, h, bpp):
    """Morton (Z-order) unswizzle used by PS3 swizzled textures (power-of-two)."""
    out = bytearray(len(px))
    def morton(x, y):
        r, bit, i = 0, 1, 0
        while (1 << i) < max(w, h):
            if (1 << i) < w: r |= ((x >> i) & 1) * bit; bit <<= 1
            if (1 << i) < h: r |= ((y >> i) & 1) * bit; bit <<= 1
            i += 1
        return r
    for y in range(h):
        for x in range(w):
            s = morton(x, y) * bpp; t = (y * w + x) * bpp
            out[t:t + bpp] = px[s:s + bpp]
    return bytes(out)

def gtf_to_png(blob, path):
    """PS3 GTF -> PNG (first texture, top mip). Handles DXT1/3/5 and A8R8G8B8."""
    from PIL import Image
    _ver, _size, n = struct.unpack(">III", blob[:12])
    _id, off, size = struct.unpack(">III", blob[12:24])
    fmt, mips = blob[24], blob[25]
    w, h = struct.unpack(">HH", blob[32:36])
    base = fmt & ~0x60  # strip LN (0x20 linear) / UN (0x40 unnormalized)
    linear = bool(fmt & 0x20)
    px = blob[off:off + size]
    if base == 0x86: img = Image.frombytes("RGBA", (w, h), px, "bcn", 1)
    elif base == 0x87: img = Image.frombytes("RGBA", (w, h), px, "bcn", 2)
    elif base == 0x88: img = Image.frombytes("RGBA", (w, h), px, "bcn", 3)
    elif base == 0x85:
        raw = px[:w * h * 4]
        if not linear: raw = _unswizzle(raw, w, h, 4)
        a, r, g, b = Image.frombytes("RGBA", (w, h), raw).split()
        img = Image.merge("RGBA", (r, g, b, a))
    else:
        return f"unsupported GTF format {fmt:#x}"
    img.save(path)
    return None

def unpack(d, out, label):
    magic = d[:4]
    hdr = struct.unpack(">13I", d[4:0x38])
    (ver, tree_off, _ts, id_off, _is, str_off, _ss, ia_off, _ias, fa_off, _fas, data_off, _ds) = hdr
    print(f"{label}: {magic.decode()} ver={ver:#x} tree@{tree_off:#x} data@{data_off:#x}")

    def cstr(off):
        e = d.index(b"\0", str_off + off)
        return d[str_off + off:e].decode("utf-8", "replace")

    def idref(off):
        # id table entries are [u32 node offset][name\0]; v1 points at the entry
        s = id_off + off + 4
        return d[s: d.index(b"\0", s)].decode("latin-1")

    os.makedirs(out, exist_ok=True)
    xml, count = [], [0]

    def node(off, depth):
        p = tree_off + off
        n_name, n_attr, _par, _prev, nxt, child, _last = struct.unpack(">7I", d[p:p + 28])
        tag = cstr(n_name)
        attrs = []
        for i in range(n_attr):
            a_name, a_type, v1, v2 = struct.unpack(">4I", d[p + 28 + i*16: p + 44 + i*16])
            an = cstr(a_name)
            if a_type == 1: val = str(v1)
            elif a_type == 2: val = repr(struct.unpack(">f", struct.pack(">I", v1))[0])
            elif a_type == 3: val = d[str_off + v1: str_off + v1 + v2].decode("utf-8", "replace").rstrip("\0")
            elif a_type == 4:  # (index, count) into int array
                val = " ".join(str(x) for x in struct.unpack(f">{v2}i", d[ia_off + v1*4: ia_off + v1*4 + v2*4]))
            elif a_type == 5:  # (index, count) into float array
                val = " ".join(f"{x:g}" for x in struct.unpack(f">{v2}f", d[fa_off + v1*4: fa_off + v1*4 + v2*4]))
            elif a_type == 8:  # reference to an id defined elsewhere
                val = "@" + idref(v1)
            elif a_type == 6:
                blob = d[data_off + v1: data_off + v1 + v2]
                if blob[:4] == b"_RAF":
                    blob = zlib.decompress(blob[8:])
                elif blob[:1] == b"\x78":
                    try: blob = zlib.decompress(blob)
                    except zlib.error: pass
                e = ext_of(blob)
                fn = f"{count[0]:03d}_{tag}_{an}.{e}"; count[0] += 1
                fp = os.path.join(out, fn)
                open(fp, "wb").write(blob)
                if e == "gim":
                    err = gim_to_png(blob, fp[:-4] + ".png")
                    if err: print(f"  {fn}: {err}")
                elif e == "gtf":
                    err = gtf_to_png(blob, fp[:-4] + ".png")
                    if err: print(f"  {fn}: {err}")
                elif e == "raf":
                    unpack(blob, fp[:-4] + "_raf", fn)
                val = fn
            elif a_type == 7: val = "#" + idref(v1)
            else: val = f"type{a_type}:{v1:#x},{v2:#x}"
            attrs.append(f"{an}={quoteattr(val)}")
        ind = "  " * depth
        a = (" " + " ".join(attrs)) if attrs else ""
        if child == 0xFFFFFFFF:
            a = f' _node="{off:#x}"' + a
        if child == 0xFFFFFFFF:
            xml.append(f"{ind}<{tag}{a}/>")
        else:
            xml.append(f"{ind}<{tag}{a}>")
            c = child
            while c != 0xFFFFFFFF:
                c = node(c, depth + 1)
            xml.append(f"{ind}</{tag}>")
        return nxt

    node(0, 0)
    open(os.path.join(out, "tree.xml"), "w", encoding="utf-8").write("\n".join(xml))
    print(f"  {label}: {count[0]} files + tree.xml -> {out}")

def main(path, out):
    d = open(path, "rb").read()
    assert d[:4] == b"P3TF", "not a P3T"
    unpack(d, out, os.path.basename(path))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
