"""Convert an unpacked PS3 dynamic-theme RAF scene (output of p3t_unpack.py) to glTF 2.0 (.glb).

Usage: python -I raf_to_gltf.py <raf_dir> <out.glb>

Edge geometry (.edge) blob layout (big-endian), reverse-engineered:
  u32 segment_count, u32 0x10 (first segment record), u32 ?, u32 total_size
  segment records, 0x80 bytes each, starting at 0x10:
    +0x00 EdgeGeomSpuConfigInfo (16 bytes): flags, holeSize, inFmt, in2Fmt, outFmt, deltaFmt,
          indexesFlavor<<4|skinFlavor, skinMatrixFmt, u16 numVertexes, u16 numIndexes, u32 -
    +0x10 u32 index_off, u16 index_size
    +0x18 u32 stream0_off (positions, float3), u32 stream1_off
    +0x20 u16 stream0_sz_a, u16 stream0_sz_b (DMA split), u16 stream1_sz_a, u16 stream1_sz_b
    +0x2C u32 stream2_off, +0x30 u16 stream2_sz_a, u16 stream2_sz_b
    +0x6C u32 desc_stream1, u32 desc_output, u32 desc_stream2, u16 sizes...
  Vertex stream descriptor: u8 count, u8 stride, ..., 8-byte decls at +8:
    [u8 offset, u8 codec, u8 count, u8 semantic(1 pos,2 normal,3 tangent,5 uv,9 weights), ...]
  Compressed index block (flavor 3):
    u16 numExplicit, u16 bias, u16 flagBytes, u8 deltaBits, u8 0
    flags: 1 bit per "new vertex slot" (1 = explicit index, 0 = next sequential vertex)
    tri codes: 2 bits per triangle; 3 = 3 new slots, 0..2 = shares edge of previous triangle + 1 slot
    explicit values: numExplicit * deltaBits bits; d = v - bias; x[i] = d (i < 8) else x[i-8] + d;
    x is the absolute index (libedgegeom_compressindexes.cpp)
    tri codes: 0 -> (p0,p2,new), 1 -> (p2,p1,new), 2 -> (p1,p0,new)
"""
import json, math, os, struct, sys
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import es01

def half(h):
    return struct.unpack(">e", struct.pack(">H", h))[0]

def bitreader(buf, base):
    def get(pos, n):
        v = 0
        for k in range(n):
            p = pos + k
            v = (v << 1) | ((buf[base + p // 8] >> (7 - p % 8)) & 1)
        return v
    return get

EDGE_MAP = (2, 1, 0)  # tri code -> edge of previous triangle

def decode_indices(blk, nv, ni):
    a, bias, c, bits = struct.unpack(">HHHB", blk[:7])
    nt = ni // 3
    L = (nt + 3) // 4
    tc = [(blk[8 + c + i // 4] >> (6 - 2 * (i % 4))) & 3 for i in range(nt)]
    get = bitreader(blk, 8 + c + L)
    # Explicit indexes: first 8 stored raw, the rest as delta vs. the value 8 earlier;
    # all offset by +bias and packed in deltaBits bits (libedgegeom_compressindexes).
    vals = []
    for i in range(a):
        d = get(i * bits, bits) - bias
        vals.append(d if i < 8 else vals[i - 8] + d)
    fl = bitreader(blk, 8)
    seq = 0; ei = 0; si = 0
    def nxt():
        nonlocal seq, ei, si
        f = fl(si, 1); si += 1
        if not f:
            seq += 1; return seq - 1
        ei += 1
        return vals[ei - 1]
    tris = []; prev = None
    for t in tc:
        if t == 3:
            tri = (nxt(), nxt(), nxt())
        else:
            p = prev
            u, v = [(p[0], p[1]), (p[1], p[2]), (p[2], p[0])][EDGE_MAP[t]]
            tri = (v, u, nxt())
        tris.append(tri); prev = tri
    return [min(max(i, 0), nv - 1) for t in tris for i in t]

def parse_desc(b, off):
    n, stride = b[off], b[off + 1]
    decls = []
    for i in range(n):
        o, codec, cnt, sem = b[off + 8 + 8 * i: off + 12 + 8 * i]
        decls.append((o, codec, cnt, sem))
    return stride, decls

def read_attr(b, base, stride, nv, o, codec, cnt):
    out = []
    for v in range(nv):
        p = base + v * stride + o
        if codec == 2:
            out.append(struct.unpack(f">{cnt}f", b[p:p + 4 * cnt]))
        elif codec == 3:
            out.append(tuple(half(h) for h in struct.unpack(f">{cnt}H", b[p:p + 2 * cnt])))
        elif codec == 1:
            out.append(tuple((2 * x + 1) / 65535 for x in struct.unpack(f">{cnt}h", b[p:p + 2 * cnt])))
        elif codec == 4:
            out.append(tuple(x / 255 for x in b[p:p + cnt]))
        elif codec == 6:
            w = struct.unpack(">I", b[p:p + 4])[0]
            def sx(val, nb):
                if val >= 1 << (nb - 1): val -= 1 << nb
                return val / ((1 << (nb - 1)) - 1)
            x, y, z = sx(w & 0x7FF, 11), sx((w >> 11) & 0x7FF, 11), sx(w >> 22, 10)
            l = math.sqrt(x * x + y * y + z * z) or 1
            out.append((x / l, y / l, z / l))
        else:
            return None
    return out

def parse_edge(b, with_skin=False):
    """Returns (pos, nrm, uv, idx) or, with with_skin, (pos, nrm, uv, idx, joints, weights).
    Skinning (per segment record): +0x34 u16 skinMatricesByteOffsets[2], +0x38 u16 skinMatricesSizes[2]
    (byte ranges into the 48-byte-per-joint matrix palette), +0x3C u16 skinIndexesAndWeightsSizes[2],
    +0x40 u32 skinIndexesAndWeights offset. Per vertex 8 bytes: 4 x (u8 weight, u8 local index);
    weights sum to 255; local index walks range 0 then range 1."""
    nseg = struct.unpack(">I", b[:4])[0]
    pos, nrm, uv, idx = [], [], [], []
    jnt, wgt = [], []
    for s in range(nseg):
        r = 0x10 + 0x80 * s
        nv, ni = struct.unpack(">HH", b[r + 8:r + 12])
        flavor = b[r + 6] >> 4
        ioff, isz = struct.unpack(">IH", b[r + 0x10:r + 0x16])
        s0, s1 = struct.unpack(">II", b[r + 0x18:r + 0x20])
        s2 = struct.unpack(">I", b[r + 0x2C:r + 0x30])[0]
        d1, _dout, d2 = struct.unpack(">III", b[r + 0x6C:r + 0x78])
        base = len(pos)
        P = [struct.unpack(">3f", b[s0 + 12 * v: s0 + 12 * v + 12]) for v in range(nv)]
        N = [None] * nv; U = [None] * nv
        for soff, doff in ((s1, d1), (s2, d2)):
            if not soff or not doff or doff + 8 > len(b) or soff >= len(b): continue
            stride, decls = parse_desc(b, doff)
            for o, codec, cnt, sem in decls:
                vals = read_attr(b, soff, stride, nv, o, codec, cnt)
                if vals is None: continue
                if sem == 2: N = vals
                elif sem == 5: U = [v[:2] for v in vals]
        if flavor == 3:
            I = decode_indices(b[ioff:ioff + isz], nv, ni)
        else:
            I = list(struct.unpack(f">{ni}H", b[ioff:ioff + 2 * ni]))
        pos += P
        nrm += [n if n else (0.0, 0.0, 1.0) for n in N]
        uv += [u if u else (0.0, 0.0) for u in U]
        idx += [i + base for i in I]
        if with_skin:
            mo = struct.unpack(">HH", b[r + 0x34:r + 0x38])
            ms = struct.unpack(">HH", b[r + 0x38:r + 0x3C])
            wsz = sum(struct.unpack(">HH", b[r + 0x3C:r + 0x40]))
            wp = struct.unpack(">I", b[r + 0x40:r + 0x44])[0]
            palette = list(range(mo[0] // 48, (mo[0] + ms[0]) // 48)) + \
                      list(range(mo[1] // 48, (mo[1] + ms[1]) // 48))
            if wsz >= 8 * nv and palette:
                for v in range(nv):
                    e = b[wp + 8 * v: wp + 8 * v + 8]
                    J = [palette[min(e[2 * k + 1], len(palette) - 1)] if e[2 * k] else 0 for k in range(4)]
                    W = [e[2 * k] / 255.0 for k in range(4)]
                    sw = sum(W) or 1.0
                    jnt.append(J); wgt.append([w / sw for w in W])
            else:
                jnt += [[0, 0, 0, 0]] * nv; wgt += [[1.0, 0, 0, 0]] * nv
    if with_skin:
        return pos, nrm, uv, idx, jnt, wgt
    return pos, nrm, uv, idx

_alpha_cache = {}
def texture_has_alpha(path):
    if path not in _alpha_cache:
        from PIL import Image
        im = Image.open(path)
        _alpha_cache[path] = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 250
    return _alpha_cache[path]

def floats(s):
    return [float(x) for x in s.split()] if s else []

def euler_to_quat(rx, ry, rz):
    cx, sx = math.cos(rx / 2), math.sin(rx / 2)
    cy, sy = math.cos(ry / 2), math.sin(ry / 2)
    cz, sz = math.cos(rz / 2), math.sin(rz / 2)
    # XYZ order
    return [sx * cy * cz - cx * sy * sz, cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz, cx * cy * cz + sx * sy * sz]

def main(raf_dir, out_path, anim_hook=None):
    root = ET.parse(os.path.join(raf_dir, "tree.xml")).getroot()
    files = {f.get("id")[1:]: f.get("src") for f in root.iter("file")}
    models = {}   # model id -> {geom, skel, invbind, anim, start, end}
    for m in root.iter("model"):
        info = {}
        for tag, key in (("geometry", "geom"), ("skeleton", "skel"), ("inv-bind", "invbind"), ("animation", "anim")):
            e = m.find(tag)
            if e is not None:
                info[key] = files.get(e.get("fileref")[1:])
                if tag == "animation":
                    info["start"] = float(e.get("start", 0)); info["end"] = float(e.get("end", 0))
        if "geom" in info:
            models[m.get("id")[1:]] = info
    textures = {t.get("id")[1:]: files.get(t.get("fileref")[1:]) for t in root.iter("texture")}
    materials = {}
    for m in root.iter("material"):
        t = m.find("_texture")
        materials[m.get("id")[1:]] = (textures.get(t.get("texref")[1:]) if t is not None else None,
                                      m.get("effect"))

    bin_ = bytearray()
    gl = {"asset": {"version": "2.0", "generator": "raf_to_gltf"}, "scene": 0,
          "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "accessors": [],
          "bufferViews": [], "materials": [], "textures": [], "images": [], "samplers": [
              {"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
          "buffers": []}

    def add_view(data, target=None):
        while len(bin_) % 4: bin_.append(0)
        off = len(bin_); bin_.extend(data)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target: v["target"] = target
        gl["bufferViews"].append(v)
        return len(gl["bufferViews"]) - 1

    def add_acc(vals, comp, typ, target, minmax=False):
        flat = [x for v in vals for x in (v if isinstance(v, (tuple, list)) else (v,))]
        fmt = "<%d%s" % (len(flat), {5126: "f", 5125: "I", 5123: "H"}[comp])
        view = add_view(struct.pack(fmt, *flat), target)
        a = {"bufferView": view, "componentType": comp, "count": len(vals), "type": typ}
        if minmax:
            n = len(vals[0])
            a["min"] = [min(v[i] for v in vals) for i in range(n)]
            a["max"] = [max(v[i] for v in vals) for i in range(n)]
        gl["accessors"].append(a)
        return len(gl["accessors"]) - 1

    image_ix = {}
    def image_for(png):
        if png not in image_ix:
            data = open(os.path.join(raf_dir, png), "rb").read()
            gl["images"].append({"bufferView": add_view(data), "mimeType": "image/png", "name": png})
            gl["textures"].append({"source": len(gl["images"]) - 1, "sampler": 0})
            image_ix[png] = len(gl["textures"]) - 1
        return image_ix[png]

    def load(name):
        return open(os.path.join(raf_dir, name), "rb").read()

    mesh_cache = {}
    def mesh_data(model_id):
        if model_id not in mesh_cache:
            info = models.get(model_id)
            if not info:
                mesh_cache[model_id] = None
            else:
                md = {"mesh": parse_edge(load(info["geom"]), with_skin=True), "skel": None, "invbind": None}
                if info.get("skel"):
                    sk = es01.parse_skeleton(load(info["skel"]))
                    if sk["num_joints"] and info.get("invbind"):
                        md["skel"] = sk
                        md["invbind"] = es01.parse_invbind(load(info["invbind"]))
                mesh_cache[model_id] = md
        return mesh_cache[model_id]

    actor_rigs = []  # (actor element, model info, [joint node indices]) for the animation pass

    scene = root.find("scene")
    for actor in scene.iter("actor"):
        mid = (actor.get("model") or "@")[1:]
        md = mesh_data(mid)
        if not md or not md["mesh"][3]:
            continue
        pos, nrm, uv, idx, jnt, wgt = md["mesh"]
        uvs = floats(actor.get("uv_scale")) or [1, 1]
        uvo = floats(actor.get("uv_offset")) or [0, 0]
        # glTF UV origin is top-left; the theme uses uv_scale (1,-1) to flip V.
        tuv = [(u * uvs[0] + uvo[0], v * -uvs[1] * -1 + uvo[1]) for u, v in uv]
        tuv = [(u, 1 - v) if uvs[1] > 0 else (u, v) for u, v in tuv]
        mat_id = (actor.get("material") or "@")[1:]
        tex, effect = materials.get(mat_id, (None, None))
        color = floats(actor.get("color")) or [1, 1, 1, 1]
        mat = {"name": mat_id, "pbrMetallicRoughness": {"baseColorFactor": color[:4],
               "metallicFactor": 0, "roughnessFactor": 1}, "alphaMode": "BLEND", "doubleSided": True}
        has_alpha = color[3] < 1.0 if len(color) > 3 else False
        if tex and os.path.exists(os.path.join(raf_dir, tex[:-4] + ".png")):
            png = tex[:-4] + ".png"
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_for(png)}
            has_alpha = has_alpha or texture_has_alpha(os.path.join(raf_dir, png))
        # Opaque textures must not be alpha-blended: blending disables depth writes and lets the
        # back of a solid model show through the front.
        mat["alphaMode"] = "BLEND" if has_alpha else "OPAQUE"
        if effect in ("pure_texture", None):
            mat["extensions"] = {"KHR_materials_unlit": {}}
        gl["materials"].append(mat)
        prim = {"attributes": {
            "POSITION": add_acc(pos, 5126, "VEC3", 34962, True),
            "NORMAL": add_acc(nrm, 5126, "VEC3", 34962),
            "TEXCOORD_0": add_acc(tuv, 5126, "VEC2", 34962)},
            "indices": add_acc(idx, 5125, "SCALAR", 34963),
            "material": len(gl["materials"]) - 1}
        sk = md["skel"]
        if sk:
            prim["attributes"]["JOINTS_0"] = add_acc(jnt, 5123, "VEC4", 34962)
            prim["attributes"]["WEIGHTS_0"] = add_acc(wgt, 5126, "VEC4", 34962)
        gl["meshes"].append({"name": mid, "primitives": [prim]})
        rot = floats(actor.get("rotation")) or [0, 0, 0]
        aname = actor.get("id", "#")[1:]
        node = {"name": aname,
                "translation": floats(actor.get("position")) or [0, 0, 0],
                "rotation": euler_to_quat(*rot), "scale": floats(actor.get("scale")) or [1, 1, 1]}
        gl["nodes"].append(node)
        actor_ix = len(gl["nodes"]) - 1
        gl["scenes"][0]["nodes"].append(actor_ix)
        if not sk:
            node["mesh"] = len(gl["meshes"]) - 1
            continue
        # Skinned: actor node -> [joint hierarchy (rest pose), mesh node with skin]
        nj = sk["num_joints"]
        first = len(gl["nodes"])
        for j in range(nj):
            gl["nodes"].append({"name": f"{aname}_j{j}", "translation": list(sk["trans"][j]),
                                "rotation": list(sk["rot"][j]), "scale": list(sk["scale"][j])})
        children = {}
        for j, p in enumerate(sk["parents"]):
            children.setdefault(p, []).append(first + j)
        for p, ch in children.items():
            parent = gl["nodes"][actor_ix] if p < 0 else gl["nodes"][first + p]
            parent.setdefault("children", []).extend(ch)
        ib = md["invbind"]
        ibm = [[float(x) for x in ib[j].T.flatten()] if j < len(ib) else [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1] for j in range(nj)]
        gl.setdefault("skins", []).append({"name": aname + "_skin", "joints": list(range(first, first + nj)),
                                           "inverseBindMatrices": add_acc(ibm, 5126, "MAT4", None),
                                           "skeleton": children[-1][0] if -1 in children else first})
        gl["nodes"].append({"name": aname + "_mesh", "mesh": len(gl["meshes"]) - 1, "skin": len(gl["skins"]) - 1})
        gl["nodes"][actor_ix].setdefault("children", []).append(len(gl["nodes"]) - 1)
        actor_rigs.append((actor, models[mid], list(range(first, first + nj))))

    if anim_hook:
        anim_hook(gl, actor_rigs, load, add_acc)

    cam = scene.find("camera")
    if cam is not None:
        gl["cameras"] = [{"type": "perspective", "perspective": {
            "yfov": float(cam.get("yfov", 0.8)), "znear": max(0.1, float(cam.get("znear", 0.1))),
            "zfar": float(cam.get("zfar", 10000)), "aspectRatio": 16 / 9}}]
        gl["nodes"].append({"name": cam.get("id", "#camera")[1:], "camera": 0,
                            "translation": floats(cam.get("position")) or [0, 0, 0]})
        gl["scenes"][0]["nodes"].append(len(gl["nodes"]) - 1)
    gl["extensionsUsed"] = ["KHR_materials_unlit"]
    # Embed the scene script (VSMX bytecode) so the viewer can run it.
    script = scene.find("script")
    if script is not None and script.get("fileref"):
        src = files.get(script.get("fileref")[1:])
        if src and os.path.exists(os.path.join(raf_dir, src)):
            import base64
            gl.setdefault("extras", {})["vsmx_base64"] = base64.b64encode(load(src)).decode()
    # Actor -> animation clip name, for script control of animation time.
    gl.setdefault("extras", {})["actors"] = [a.get("id", "#")[1:] for a in scene.iter("actor")]

    while len(bin_) % 4: bin_.append(0)
    gl["buffers"].append({"byteLength": len(bin_)})
    js = json.dumps(gl).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
        f.write(struct.pack("<II", len(bin_), 0x004E4942)); f.write(bin_)
    print(f"wrote {out_path}: {len(gl['nodes'])} nodes, {len(gl['meshes'])} meshes, "
          f"{len(gl['images'])} textures, {len(bin_) // 1024} KiB")

def ea03_anim_hook(gl, actor_rigs, load, add_acc):
    """Add one glTF animation per animated actor (decoded EA03, sampled per frame).
    The actor's anim_speed is stored in animation.extras.speed for the viewer."""
    import ea03
    cache = {}
    time_acc = {}
    for actor, info, joints in actor_rigs:
        src = info.get("anim")
        if not src:
            continue
        if src not in cache:
            cache[src] = ea03.decode_anim(load(src))
        a = cache[src]
        samplers, channels = [], []
        for j, tracks in a["joints"].items():
            if j >= len(joints):
                continue
            for key, path, typ in (("r", "rotation", "VEC4"), ("t", "translation", "VEC3"), ("s", "scale", "VEC3")):
                keys = tracks.get(key)
                if not keys:
                    continue
                times = [float(t) for t, _ in keys]
                tk = (len(times), times[0], times[-1])
                if tk not in time_acc:
                    time_acc[tk] = add_acc(times, 5126, "SCALAR", None, False)
                    gl["accessors"][time_acc[tk]]["min"] = [times[0]]
                    gl["accessors"][time_acc[tk]]["max"] = [times[-1]]
                out = add_acc([[float(x) for x in v] for _, v in keys], 5126, typ, None)
                samplers.append({"input": time_acc[tk], "output": out, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": joints[j], "path": path}})
        if channels:
            speed = floats(actor.get("anim_speed")) or [1.0]
            gl.setdefault("animations", []).append({
                "name": actor.get("id", "#")[1:], "samplers": samplers, "channels": channels,
                "extras": {"speed": speed[0], "duration": a["duration"]}})

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], anim_hook=None if "--static" in sys.argv else ea03_anim_hook)
