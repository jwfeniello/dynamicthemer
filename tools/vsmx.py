"""VSMX (compiled theme/RCO JavaScript) disassembler.

Header (little-endian u32): 'VSMX', version (0x20000), codeOff, codeLen, textOff, textLen, textCount,
propOff, propLen, propCount, namesOff, namesLen, namesCount.
Code: 8-byte instructions (u32 opcode, u32 operand). Tables: UTF-16LE strings, NUL-terminated.
Opcode names extracted from raf_script.exe (pointer table @0x487110).
Usage: python -I vsmx.py file.vsmx [--raw]
"""
import struct, sys

OPS = ("NOP ASSGN ADD SUB MUL DIV MOD TO_NUMBER CSIGN NOT INC DEC POST_INC POST_DEC CMPEQ CMPNEQ CMPSEQ "
       "CMPSNEQ CMPLT CMPLE CMPGE CMPGT INSTANCEOF IN TYPEOF BIT_AND BIT_XOR BIT_OR BIT_NOT LSHIFT S_RSHIFT "
       "U_RSHIFT COPY SWAP REMOVE PUSH_UNDEFINED PUSH_NULL PUSH_BOOL PUSH_INT PUSH_FLOAT PUSH_STRING "
       "PUSH_OBJECT PUSH_FUNC PUSH_ARRAY PUSH_THIS PUSH_LOCAL PUSH_GLOBAL GETATTR GETATTR_KEEPOBJ SETATTR "
       "DELATTR APPEND_ATTR GETITEM GETITEM_KEEPOBJ SETITEM DELITEM APPEND_ITEM JUMP JUMPT JUMPF CALL_FUNC "
       "CALL_METHOD CALL_CONSTRUCTOR RET THROW TRYBLK_IN TRYBLK_OUT CATCH_FINALLYBLK_IN CATCH_FINALLYBLK_OUT "
       "HALT DEBUG_FILE DEBUG_LINE GETITEM_KEEPOBJNAME PUSH_VECTOR GET_VECTOR_ELEMENT "
       "GET_VECTOR_ELEMENT_KEEPVECTOR ASSGN_VECTOR_ELEMENT SETATTR_VECTOR_ELEMENT SETITEM_VECTOR_ELEMENT").split()

def table(b, off, length):
    """Return {offset: string} for a NUL-separated table; UTF-16LE (offsets in chars) unless it
    looks like 8-bit text (the names table in v2 files is ASCII, offsets in bytes)."""
    chunk = b[off:off + length]
    wide = len(chunk) > 1 and chunk[1] == 0
    raw = chunk.decode("utf-16-le" if wide else "latin-1", "replace")
    return dict(enumerate(raw.split("\0")))

def parse(b):
    assert b[:4] == b"VSMX"
    h = struct.unpack("<12I", b[4:0x34])
    ver, co, cl, to, tl, tn, po, pl, pn, no, nl, nn = h
    text, prop, names = table(b, to, tl), table(b, po, pl), table(b, no, nl)
    code = [struct.unpack("<II", b[co + 8 * i: co + 8 * i + 8]) for i in range(cl // 8)]
    return {"version": ver, "code": code, "text": text, "prop": prop, "names": names}

def operand(op, v, t):
    name = OPS[op & 0xFF] if (op & 0xFF) < len(OPS) else f"OP_{op:#x}"
    if name == "PUSH_STRING": return repr(t["text"].get(v, f"?text@{v}"))
    if name == "PUSH_GLOBAL": return t["names"].get(v, f"?name@{v}")
    if name == "PUSH_LOCAL": return f"local{v}"
    if name in ("GETATTR", "GETATTR_KEEPOBJ", "SETATTR", "DELATTR", "APPEND_ATTR", "CALL_METHOD_NAME"):
        return t["prop"].get(v, f"?prop@{v}")
    if name == "PUSH_FLOAT": return repr(struct.unpack("<f", struct.pack("<I", v))[0])
    if name == "PUSH_INT": return str(struct.unpack("<i", struct.pack("<I", v))[0])
    if name in ("JUMP", "JUMPT", "JUMPF"): return f"-> {v:04d}"
    return str(v) if v else ""

def disasm(b, raw=False):
    t = parse(b)
    lines = []
    for i, (op, v) in enumerate(t["code"]):
        name = OPS[op & 0xFF] if (op & 0xFF) < len(OPS) else f"OP_{op:#x}"
        hi = f" [hi={op >> 8:#x}]" if op >> 8 else ""
        extra = f"   ; raw {op:08x} {v:08x}" if raw else ""
        lines.append(f"{i:04d} {name:<22}{operand(op, v, t)}{hi}{extra}")
    return lines, t

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    data = open(sys.argv[1], "rb").read()
    lines, t = disasm(data, "--raw" in sys.argv)
    print(f"; VSMX v{t['version']:#x}: {len(t['code'])} instrs, {len(t['text'])} text, "
          f"{len(t['prop'])} props, {len(t['names'])} names")
    print("\n".join(lines))
