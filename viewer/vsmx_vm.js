// VSMX bytecode interpreter (PS3 dynamic-theme / RCO JavaScript), host-agnostic.
// Opcode table and semantics: see docs/ps3-dynamic-theme-formats.md section 10.
const OPS = ("NOP ASSGN ADD SUB MUL DIV MOD TO_NUMBER CSIGN NOT INC DEC POST_INC POST_DEC CMPEQ CMPNEQ CMPSEQ " +
  "CMPSNEQ CMPLT CMPLE CMPGE CMPGT INSTANCEOF IN TYPEOF BIT_AND BIT_XOR BIT_OR BIT_NOT LSHIFT S_RSHIFT " +
  "U_RSHIFT COPY SWAP REMOVE PUSH_UNDEFINED PUSH_NULL PUSH_BOOL PUSH_INT PUSH_FLOAT PUSH_STRING " +
  "PUSH_OBJECT PUSH_FUNC PUSH_ARRAY PUSH_THIS PUSH_LOCAL PUSH_GLOBAL GETATTR GETATTR_KEEPOBJ SETATTR " +
  "DELATTR APPEND_ATTR GETITEM GETITEM_KEEPOBJ SETITEM DELITEM APPEND_ITEM JUMP JUMPT JUMPF CALL_FUNC " +
  "CALL_METHOD CALL_CONSTRUCTOR RET THROW TRYBLK_IN TRYBLK_OUT CATCH_FINALLYBLK_IN CATCH_FINALLYBLK_OUT " +
  "HALT DEBUG_FILE DEBUG_LINE GETITEM_KEEPOBJNAME PUSH_VECTOR GET_VECTOR_ELEMENT " +
  "GET_VECTOR_ELEMENT_KEEPVECTOR ASSGN_VECTOR_ELEMENT SETATTR_VECTOR_ELEMENT SETITEM_VECTOR_ELEMENT").split(" ");
const OP = Object.fromEntries(OPS.map((n, i) => [n, i]));

export function parseVSMX(buf) {
  const u8 = new Uint8Array(buf), dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const magic = String.fromCharCode(...u8.slice(0, 4));
  if (magic !== "VSMX") throw new Error("not VSMX");
  const h = []; for (let i = 0; i < 12; i++) h.push(dv.getUint32(4 + 4 * i, true));
  const [ver, co, cl, to, tl, , po, pl, , no, nl] = h;
  const table = (off, len) => {
    const c = u8.slice(off, off + len);
    const wide = c.length > 1 && c[1] === 0;
    return new TextDecoder(wide ? "utf-16le" : "latin1").decode(c).split("\0");
  };
  const code = [];
  for (let i = 0; i < cl / 8; i++) code.push([dv.getUint32(co + 8 * i, true), dv.getUint32(co + 8 * i + 4, true)]);
  return { version: ver, code, text: table(to, tl), prop: table(po, pl), names: table(no, nl) };
}

class Ref {
  constructor(obj, key) { this.obj = obj; this.key = key; }
  get() { return this.obj[this.key]; }
  set(v) { this.obj[this.key] = v; }
}
const deref = v => (v instanceof Ref ? v.get() : v);

export class Func {
  constructor(vm, pc, argc, nloc, name) { Object.assign(this, { vm, pc, argc, nloc, name }); }
}
class Throw { constructor(v) { this.value = v; } }

export class VSMX {
  constructor(bytes, globals, { log = console.log, maxSteps = 5e6 } = {}) {
    this.p = parseVSMX(bytes);
    this.g = globals;
    this.log = log;
    this.maxSteps = maxSteps;
  }
  // Run the top-level program (until HALT).
  start() { return this.run(0, [], undefined, true); }

  call(f, thisv, args) {
    if (f instanceof Func) {
      const locals = new Array(Math.max(f.nloc, f.argc + 1)).fill(undefined);
      locals[0] = args; // slot 0: arguments
      for (let i = 0; i < f.argc; i++) locals[i + 1] = args[i];
      return this.run(f.pc, locals, thisv, false);
    }
    if (typeof f === "function") return f.apply(thisv, args);
    throw new TypeError("not a function: " + String(f));
  }

  construct(c, args) {
    if (c instanceof Func) {
      const obj = {};
      const r = this.call(c, obj, args);
      return (r && typeof r === "object") ? r : obj;
    }
    if (typeof c === "function") return Reflect.construct(c, args);
    throw new TypeError("not a constructor: " + String(c));
  }

  run(pc, locals, thisv, top) {
    const { code, text, prop, names } = this.p;
    const g = this.g, st = [], handlers = [];
    const pop = () => deref(st.pop());
    let steps = 0;
    for (;;) {
      if (++steps > this.maxSteps) throw new Error("VSMX step limit");
      const [opw, v] = code[pc];
      const op = opw & 0xff, hi = opw >>> 8;
      pc++;
      try {
        switch (op) {
          case OP.NOP: case OP.DEBUG_FILE: case OP.DEBUG_LINE: case OP.CATCH_FINALLYBLK_OUT: break;
          case OP.ASSGN: { const val = pop(); const r = st.pop(); r.set(val); st.push(val); break; }
          case OP.ADD: { const b = pop(), a = pop(); st.push(a + b); break; }
          case OP.SUB: { const b = pop(), a = pop(); st.push(a - b); break; }
          case OP.MUL: { const b = pop(), a = pop(); st.push(a * b); break; }
          case OP.DIV: { const b = pop(), a = pop(); st.push(a / b); break; }
          case OP.MOD: { const b = pop(), a = pop(); st.push(a % b); break; }
          case OP.TO_NUMBER: st.push(+pop()); break;
          case OP.CSIGN: st.push(-pop()); break;
          case OP.NOT: st.push(!pop()); break;
          case OP.BIT_NOT: st.push(~pop()); break;
          case OP.TYPEOF: { const x = pop(); st.push(x instanceof Func ? "function" : typeof x); break; }
          case OP.INC: case OP.DEC: case OP.POST_INC: case OP.POST_DEC: {
            const r = st.pop(); const old = +deref(r);
            const nv = (op === OP.INC || op === OP.POST_INC) ? old + 1 : old - 1;
            r.set(nv); st.push(op === OP.INC || op === OP.DEC ? nv : old); break;
          }
          case OP.CMPEQ: { const b = pop(), a = pop(); st.push(a == b); break; }
          case OP.CMPNEQ: { const b = pop(), a = pop(); st.push(a != b); break; }
          case OP.CMPSEQ: { const b = pop(), a = pop(); st.push(a === b); break; }
          case OP.CMPSNEQ: { const b = pop(), a = pop(); st.push(a !== b); break; }
          case OP.CMPLT: { const b = pop(), a = pop(); st.push(a < b); break; }
          case OP.CMPLE: { const b = pop(), a = pop(); st.push(a <= b); break; }
          case OP.CMPGE: { const b = pop(), a = pop(); st.push(a >= b); break; }
          case OP.CMPGT: { const b = pop(), a = pop(); st.push(a > b); break; }
          case OP.INSTANCEOF: { const b = pop(), a = pop(); st.push(typeof b === "function" ? a instanceof b : false); break; }
          case OP.IN: { const b = pop(), a = pop(); st.push(a in Object(b)); break; }
          case OP.BIT_AND: { const b = pop(), a = pop(); st.push(a & b); break; }
          case OP.BIT_XOR: { const b = pop(), a = pop(); st.push(a ^ b); break; }
          case OP.BIT_OR: { const b = pop(), a = pop(); st.push(a | b); break; }
          case OP.LSHIFT: { const b = pop(), a = pop(); st.push(a << b); break; }
          case OP.S_RSHIFT: { const b = pop(), a = pop(); st.push(a >> b); break; }
          case OP.U_RSHIFT: { const b = pop(), a = pop(); st.push(a >>> b); break; }
          case OP.COPY: st.push(st[st.length - 1]); break;
          case OP.SWAP: { const b = st.pop(), a = st.pop(); st.push(b, a); break; }
          case OP.REMOVE: st.pop(); break;
          case OP.PUSH_UNDEFINED: st.push(undefined); break;
          case OP.PUSH_NULL: st.push(null); break;
          case OP.PUSH_BOOL: st.push(v !== 0); break;
          case OP.PUSH_INT: st.push(v | 0); break;
          case OP.PUSH_FLOAT: { const f = new DataView(new ArrayBuffer(4)); f.setUint32(0, v); st.push(f.getFloat32(0)); break; }
          case OP.PUSH_STRING: st.push(text[v]); break;
          case OP.PUSH_OBJECT: st.push({}); break;
          case OP.PUSH_ARRAY: st.push([]); break;
          case OP.PUSH_FUNC: st.push(new Func(this, v, hi & 0xff, hi >>> 16)); break;
          case OP.PUSH_THIS: st.push(thisv); break;
          case OP.PUSH_LOCAL: st.push(new Ref(locals, v)); break;
          case OP.PUSH_GLOBAL: st.push(new Ref(g, names[v])); break;
          // Attribute/item reads yield references so they can be targets of ASSGN / INC (e.g. o.n++).
          case OP.GETATTR: { const o = pop(); st.push(o == null ? undefined : new Ref(Object(o), prop[v])); break; }
          case OP.GETATTR_KEEPOBJ: { const o = pop(); st.push(o, o == null ? undefined : o[prop[v]]); break; }
          case OP.SETATTR: { const val = pop(), o = pop(); o[prop[v]] = val; st.push(val); break; }
          case OP.DELATTR: { const o = pop(); st.push(delete o[prop[v]]); break; }
          case OP.APPEND_ATTR: { const val = pop(); st[st.length - 1][prop[v]] = val; break; }
          case OP.GETITEM: { const k = pop(), o = pop(); st.push(o == null ? undefined : new Ref(Object(o), k)); break; }
          case OP.GETITEM_KEEPOBJ: case OP.GETITEM_KEEPOBJNAME: { const k = pop(), o = pop(); st.push(o, o[k]); break; }
          case OP.SETITEM: { const val = pop(), k = pop(), o = pop(); o[k] = val; st.push(val); break; }
          case OP.DELITEM: { const k = pop(), o = pop(); st.push(delete o[k]); break; }
          case OP.APPEND_ITEM: { const val = pop(); st[st.length - 1].push(val); break; }
          case OP.JUMP: pc = v; break;
          case OP.JUMPT: if (pop()) pc = v; break;
          case OP.JUMPF: if (!pop()) pc = v; break;
          case OP.CALL_FUNC: { const args = st.splice(st.length - v, v).map(deref); const f = pop(); st.push(this.call(f, undefined, args)); break; }
          case OP.CALL_METHOD: { const args = st.splice(st.length - v, v).map(deref); const f = pop(), o = pop(); st.push(this.call(f, o, args)); break; }
          case OP.CALL_CONSTRUCTOR: { const args = st.splice(st.length - v, v).map(deref); const c = pop(); st.push(this.construct(c, args)); break; }
          case OP.RET: return st.length ? pop() : undefined;
          case OP.HALT: return undefined;
          case OP.THROW: throw new Throw(pop());
          case OP.TRYBLK_IN: handlers.push({ pc: v, sp: st.length }); break;
          case OP.TRYBLK_OUT: handlers.pop(); break;
          case OP.CATCH_FINALLYBLK_IN: pc = v; break; // reached normally: skip the catch block
          case OP.PUSH_VECTOR: st.push(st.splice(st.length - v, v).map(deref)); break;
          case OP.GET_VECTOR_ELEMENT: { const o = pop(); st.push(o[hi]); break; }
          case OP.GET_VECTOR_ELEMENT_KEEPVECTOR: { const o = pop(); st.push(o, o[hi]); break; }
          case OP.ASSGN_VECTOR_ELEMENT: { const val = pop(), r = st.pop(); deref(r)[hi] = val; st.push(val); break; }
          case OP.SETATTR_VECTOR_ELEMENT: { const val = pop(), o = pop(); o[prop[v]][hi] = val; st.push(val); break; }
          case OP.SETITEM_VECTOR_ELEMENT: { const val = pop(), k = pop(), o = pop(); o[k][hi] = val; st.push(val); break; }
          default: throw new Error(`VSMX: unimplemented ${OPS[op] || op} @${pc - 1}`);
        }
      } catch (e) {
        const h = handlers.pop();
        if (!h) throw e;
        st.length = h.sp;
        const val = e instanceof Throw ? e.value : e;
        st.push(val);
        pc = h.pc + 1; // handler starts after the CATCH_FINALLYBLK_IN marker
      }
    }
  }
}
