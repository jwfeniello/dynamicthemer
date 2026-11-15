"""Decrypt a PS3 NPD EDAT using a .rap license.
Usage: python -I ps3_edat_decrypt.py in.edat license.rap out.bin"""
import struct, sys
from Crypto.Cipher import AES

RAP_KEY = bytes.fromhex("869F7745C13FD890CCF29188E3CC3EDF")
RAP_PBOX = [0x0C,0x03,0x06,0x04,0x01,0x0B,0x0F,0x08,0x02,0x07,0x00,0x05,0x0A,0x0E,0x0D,0x09]
RAP_E1 = bytes.fromhex("A93E1FD67C55A329B75FDDA62A95C7A5")
RAP_E2 = bytes.fromhex("67D45DA3296D006A4E7C537BF5538C74")
EDAT_KEY0 = bytes.fromhex("BE959CA8308DEFA2E5E180C63712A9AE")
EDAT_IV = bytes(16)

def rap_to_klic(rap):
    key = bytearray(AES.new(RAP_KEY, AES.MODE_ECB).decrypt(rap))
    for _ in range(5):
        for p in RAP_PBOX:
            key[p] ^= RAP_E1[p]
        for i in range(15, 0, -1):
            key[RAP_PBOX[i]] ^= key[RAP_PBOX[i - 1]]
        o = 0
        for p in RAP_PBOX:
            kc = (key[p] - o) & 0xFF
            ec2 = RAP_E2[p]
            if o != 1 or kc != 0xFF:
                o = 1 if kc < ec2 else 0
                key[p] = (kc - ec2) & 0xFF
            else:
                key[p] = (kc - ec2) & 0xFF
    return bytes(key)

def main(edat_path, rap_path, out_path):
    d = open(edat_path, "rb").read()
    assert d[:4] == b"NPD\0"
    version, license_, typ = struct.unpack(">III", d[4:16])
    digest, dev_hash = d[0x40:0x50], d[0x60:0x70]
    flags, bsize, fsize = struct.unpack(">IIQ", d[0x80:0x90])
    print(f"NPD v{version} license={license_} type={typ} flags={flags:#x} block={bsize:#x} size={fsize}")
    if flags & 1:
        sys.exit("compressed EDAT not supported")
    klic = rap_to_klic(open(rap_path, "rb").read())
    nblocks = (fsize + bsize - 1) // bsize
    meta = 0x20 if flags & 0x20 else 0x10
    out = bytearray()
    for i in range(nblocks):
        length = min(bsize, fsize - i * bsize)
        enc_len = (length + 15) & ~15
        if flags & 0x20:
            off = 0x100 + i * (bsize + meta) + meta
        else:
            off = 0x100 + nblocks * meta + i * bsize
        bkey = (dev_hash[:12] if version > 1 else bytes(12)) + struct.pack(">I", i)
        k = AES.new(klic, AES.MODE_ECB).encrypt(bkey)
        if flags & 0x8:  # encrypted key flag
            k = AES.new(EDAT_KEY0, AES.MODE_CBC, EDAT_IV).decrypt(k)
        out += AES.new(k, AES.MODE_CBC, digest).decrypt(d[off:off + enc_len])[:length]
    open(out_path, "wb").write(out)
    print("wrote", out_path, "magic:", out[:16].hex(" "))

if __name__ == "__main__":
    main(*sys.argv[1:4])
