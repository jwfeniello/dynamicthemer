"""Extract a PS3 retail PKG (type 0x0001). Usage: python -I ps3_pkg_extract.py in.pkg outdir"""
import os, struct, sys
from Crypto.Cipher import AES
from Crypto.Util import Counter

PS3_KEY = bytes.fromhex("2E7B71D7C9C9A14EA3221F188828B8F8")

def main(path, out):
    f = open(path, "rb")
    h = f.read(0xC0)
    assert h[:4] == b"\x7fPKG"
    rev, typ = struct.unpack(">HH", h[4:8])
    item_count = struct.unpack(">I", h[0x14:0x18])[0]
    data_off, data_size = struct.unpack(">QQ", h[0x20:0x30])
    content_id = h[0x30:0x60].rstrip(b"\0").decode()
    riv = int.from_bytes(h[0x70:0x80], "big")
    print(f"rev={rev:#x} type={typ} items={item_count} content_id={content_id}")

    def dec(off, size):  # off relative to data_off
        start = off & ~15
        f.seek(data_off + start)
        raw = f.read(size + (off - start) + 15 & ~15 if False else ((off - start + size + 15) & ~15))
        c = AES.new(PS3_KEY, AES.MODE_CTR, counter=Counter.new(128, initial_value=riv + start // 16))
        return c.decrypt(raw)[off - start: off - start + size]

    table = dec(0, item_count * 32)
    os.makedirs(out, exist_ok=True)
    for i in range(item_count):
        no, ns, do, ds, flags, _ = struct.unpack(">IIQQII", table[i*32:(i+1)*32])
        name = dec(no, ns).decode("utf-8", "replace")
        kind = flags & 0xFF
        print(f"  [{i}] {name!r} size={ds} flags={flags:#x}")
        dst = os.path.join(out, name.replace("/", os.sep))
        if kind == 4:  # directory
            os.makedirs(dst, exist_ok=True); continue
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        with open(dst, "wb") as o:
            o.write(dec(do, ds))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
