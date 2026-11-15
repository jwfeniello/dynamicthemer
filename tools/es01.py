"""PS3 Edge Animation skeleton (ES01) + inverse-bind (.invbind) readers.

ES01 layout (big-endian), offsets at +0x14.. are self-relative (field address + value):
  0x00 'ES01'  0x04 u32 size?  0x08 u32 total size
  0x0C u16 numJoints   0x10 u16 numUserChannels?
  0x14 -> base pose: numJoints x 0x30 = quat xyzw f32, trans xyzw f32, scale xyzw f32 (joint-local)
  0x18 -> u16 parent index per joint (0xFFFF = root)
  0x1C -> u32 joint name hash per joint
  0x30.. SIMD hierarchy processing table (not needed to evaluate)
.invbind: numJoints x 3x4 f32 row-major matrices (rows: [r00 r01 r02 tx] ...)
"""
import struct
import numpy as np

def rel(b, field):
    return field + struct.unpack(">I", b[field:field + 4])[0]

def parse_skeleton(b):
    assert b[:4] == b"ES01", b[:4]
    nj = struct.unpack(">H", b[0x0C:0x0E])[0]
    if nj == 0:
        return {"num_joints": 0, "parents": [], "rot": [], "trans": [], "scale": [], "hashes": []}
    bp, pp, hp = rel(b, 0x14), rel(b, 0x18), rel(b, 0x1C)
    rot, trans, scale = [], [], []
    for j in range(nj):
        v = struct.unpack(">12f", b[bp + 0x30 * j: bp + 0x30 * j + 0x30])
        rot.append(v[0:4]); trans.append(v[4:7]); scale.append(v[8:11])
    parents = [p if p != 0xFFFF else -1 for p in struct.unpack(f">{nj}H", b[pp:pp + 2 * nj])]
    hashes = list(struct.unpack(f">{nj}I", b[hp:hp + 4 * nj]))
    return {"num_joints": nj, "parents": parents, "rot": rot, "trans": trans, "scale": scale, "hashes": hashes}

def parse_invbind(b):
    n = len(b) // 48
    out = []
    for j in range(n):
        r = np.array(struct.unpack(">12f", b[48 * j:48 * j + 48]), dtype=float).reshape(3, 4)
        m = np.eye(4); m[:3, :] = r
        out.append(m)
    return out

def quat_to_mat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])

def trs(t, q, s):
    m = np.eye(4)
    m[:3, :3] = quat_to_mat(q) * np.array(s)[None, :]
    m[:3, 3] = t
    return m

def world_matrices(sk):
    W = []
    for j in range(sk["num_joints"]):
        L = trs(sk["trans"][j], sk["rot"][j], sk["scale"][j])
        p = sk["parents"][j]
        W.append(L if p < 0 else W[p] @ L)
    return W

if __name__ == "__main__":
    import sys
    sk = parse_skeleton(open(sys.argv[1], "rb").read())
    print("joints", sk["num_joints"], "parents", sk["parents"][:20])
    if len(sys.argv) > 2:
        ib = parse_invbind(open(sys.argv[2], "rb").read())
        W = world_matrices(sk)
        err = [np.abs(W[j] @ ib[j] - np.eye(4)).max() for j in range(min(len(W), len(ib)))]
        print("max |world*invbind - I| per joint: max=%.4g median=%.4g" % (max(err), float(np.median(err))))
