'use strict';
// Minimal gRPC-Web client: protobuf wire format driven by window.HARDWARE_SCHEMA (generated from the
// .proto), unary calls only. Envoy translates these requests to native gRPC for the backend.
(() => {
  const SCHEMA = window.HARDWARE_SCHEMA, text = new TextEncoder(), utf8 = new TextDecoder();
  const CODES = {0:'OK', 3:'INVALID_ARGUMENT', 4:'DEADLINE_EXCEEDED', 5:'NOT_FOUND', 6:'ALREADY_EXISTS', 7:'PERMISSION_DENIED',
    8:'RESOURCE_EXHAUSTED', 9:'FAILED_PRECONDITION', 10:'ABORTED', 12:'UNIMPLEMENTED', 13:'INTERNAL', 14:'UNAVAILABLE', 16:'UNAUTHENTICATED'};

  class RpcError extends Error {
    constructor(code, message) { super(message); this.code = code; this.name = CODES[code] || 'UNKNOWN'; }
  }

  function varint(out, value) {
    let n = BigInt.asUintN(64, BigInt(value));
    while (n > 0x7fn) { out.push(Number(n & 0x7fn) | 0x80); n >>= 7n; }
    out.push(Number(n));
  }
  function bytes(out, data) { varint(out, data.length); for (const b of data) out.push(b); }

  function encode(name, object) {
    const out = [];
    for (const f of SCHEMA.messages[name]) {
      const value = object[f.name];
      if (value === undefined || value === null) continue;
      // proto3 leaves default values off the wire unless the field tracks presence (`optional`).
      if (!f.repeated && !f.optional && f.type !== 'message' && (value === 0 || value === '' || value === false)) continue;
      for (const item of f.repeated ? value : [value]) {
        if (f.type === 'string') { varint(out, f.n << 3 | 2); bytes(out, text.encode(item)); }
        else if (f.type === 'int64' || f.type === 'int32') varint(out, f.n << 3), varint(out, item);
        else if (f.type === 'bool') varint(out, f.n << 3), varint(out, item ? 1 : 0);
        else if (f.type === 'message') { varint(out, f.n << 3 | 2); bytes(out, encode(f.message, item)); }
        else throw new Error('Cannot encode ' + f.type);
      }
    }
    return out;
  }

  // Split a message into [fieldNumber, wireType, value]: BigInt for varints, Uint8Array otherwise.
  function fieldsOf(buffer) {
    const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength), found = [];
    let i = 0;
    const read = () => { let n = 0n, shift = 0n, b; do { b = buffer[i++]; n |= BigInt(b & 0x7f) << shift; shift += 7n; } while (b & 0x80); return n; };
    while (i < buffer.length) {
      const key = Number(read()), num = key >> 3, wire = key & 7;
      if (wire === 0) found.push([num, wire, read()]);
      else if (wire === 1) { found.push([num, wire, buffer.subarray(i, i + 8)]); i += 8; }
      else if (wire === 2) { const n = Number(read()); found.push([num, wire, buffer.subarray(i, i + n)]); i += n; }
      else if (wire === 5) { found.push([num, wire, buffer.subarray(i, i + 4)]); i += 4; }
      else throw new Error('Unsupported wire type ' + wire);
    }
    void view;
    return found;
  }
  const double = raw => new DataView(raw.buffer, raw.byteOffset, 8).getFloat64(0, true);
  const signed = big => Number(BigInt.asIntN(64, big));

  // google.protobuf.Struct / Value / ListValue -> plain JSON values.
  function struct(buffer) {
    const object = {};
    for (const [num, , entry] of fieldsOf(buffer)) {
      if (num !== 1) continue;
      let key = '', value = null;
      for (const [n, , v] of fieldsOf(entry)) { if (n === 1) key = utf8.decode(v); else if (n === 2) value = jsonValue(v); }
      object[key] = value;
    }
    return object;
  }
  function jsonValue(buffer) {
    let result = null;
    for (const [num, , v] of fieldsOf(buffer)) {
      if (num === 2) result = double(v);
      else if (num === 3) result = utf8.decode(v);
      else if (num === 4) result = v !== 0n;
      else if (num === 5) result = struct(v);
      else if (num === 6) result = fieldsOf(v).filter(f => f[0] === 1).map(f => jsonValue(f[2]));
    }
    return result;
  }

  function decode(name, buffer) {
    const fields = SCHEMA.messages[name], result = {};
    for (const f of fields) {
      if (f.optional) continue;
      if (f.repeated) result[f.name] = [];
      else if (f.type === 'string') result[f.name] = '';
      else if (f.type === 'bool') result[f.name] = false;
      else if (f.type !== 'message' && !f.optional) result[f.name] = 0;
    }
    for (const [num, , raw] of fieldsOf(buffer)) {
      const f = fields.find(x => x.n === num);
      if (!f) continue;
      let value;
      if (f.type === 'string') value = utf8.decode(raw);
      else if (f.type === 'int64' || f.type === 'int32') value = signed(raw);
      else if (f.type === 'bool') value = raw !== 0n;
      else if (f.type === 'double') value = double(raw);
      else if (f.message === 'google.protobuf.Struct') value = struct(raw);
      else value = decode(f.message, raw);
      if (f.repeated) result[f.name].push(value); else result[f.name] = value;
    }
    return result;
  }

  function trailers(data) {
    const found = {};
    for (const line of utf8.decode(data).split('\r\n')) { const at = line.indexOf(':'); if (at > 0) found[line.slice(0, at).trim().toLowerCase()] = line.slice(at + 1).trim(); }
    return found;
  }

  async function call(method, request = {}, headers = {}) {
    const spec = SCHEMA.methods[method];
    if (!spec) throw new Error('Unknown RPC ' + method);
    const message = encode(spec.request, request), body = new Uint8Array(5 + message.length);
    new DataView(body.buffer).setUint32(1, message.length);
    body.set(message, 5);
    let response;
    try {
      response = await fetch(`/${spec.service}/${method}`, {method: 'POST', credentials: 'omit', body, headers: {
        'Content-Type': 'application/grpc-web+proto', 'Accept': 'application/grpc-web+proto', 'X-Grpc-Web': '1', ...headers}});
    } catch (e) { throw new RpcError(14, 'Network error; retry with the same request'); }
    if (!response.ok) throw new RpcError(response.status === 413 ? 8 : 14, `Service unavailable (HTTP ${response.status})`);
    const data = new Uint8Array(await response.arrayBuffer());
    let status = response.headers.get('grpc-status'), details = response.headers.get('grpc-message') || '', payload = new Uint8Array();
    for (let i = 0; i + 5 <= data.length;) {
      const length = new DataView(data.buffer, data.byteOffset + i + 1, 4).getUint32(0), frame = data.subarray(i + 5, i + 5 + length);
      if (data[i] & 0x80) { const t = trailers(frame); status = t['grpc-status'] ?? status; details = t['grpc-message'] ?? details; }
      else payload = frame;
      i += 5 + length;
    }
    if (status !== '0') throw new RpcError(Number(status ?? 2), decodeURIComponent(details) || 'Request failed');
    return decode(spec.response, payload);
  }

  // Timestamps travel as {seconds, nanos}.
  const toTimestamp = iso => { const ms = Date.parse(iso); return Number.isNaN(ms) ? null : {seconds: Math.floor(ms / 1000), nanos: (ms % 1000) * 1e6}; };
  const toIso = t => t && (t.seconds || t.nanos) ? new Date(t.seconds * 1000 + Math.floor(t.nanos / 1e6)).toISOString() : '';

  window.rpc = {call, RpcError, toTimestamp, toIso, encode, decode};
})();
