// The browser's hand-written protobuf codec must agree byte-for-byte with the real protobuf library.
// Golden bytes below were produced by Python's protobuf for the same messages.
// Run: python scripts/generate-ui-schema.py && node --test scripts/tests/rpc-codec.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
globalThis.window = globalThis;
require('../../ui/schema.js');
require('../../ui/rpc.js');
const {encode, decode, toIso, toTimestamp} = window.rpc;
const hex = h => Uint8Array.from(Buffer.from(h, 'hex'));
const toHex = a => Buffer.from(a).toString('hex');

test('decodes a list of hardware including derived fields and non-ASCII ids', () => {
  const reply = decode('hardware.v1.ListHardwareResponse', hex(
    '0a320a05682dc3a93112064c6170746f701a08436f6d70757465722a03412d313a06616374697665400a48035001580460096807103918022019'));
  assert.equal(reply.total, 57); assert.equal(reply.page, 2); assert.equal(reply.size, 25);
  const h = reply.items[0];
  assert.deepEqual([h.id, h.name, h.status, h.asset_tag], ['h-é1', 'Laptop', 'active', 'A-1']);
  assert.deepEqual([h.total, h.reserved, h.out, h.version, h.available, h.unallocated], [10, 3, 1, 4, 9, 7]);
  assert.equal(h.description, '');
});

test('decodes audit events: optional strings, timestamps and Struct snapshots', () => {
  const event = decode('hardware.v1.ListTransactionsResponse', hex(
    '0aae010a02653112067465616d3a781a0661646a7573742201682a01683a02753142037768794a0c08f99eadd7061080cab5ee0152500a4e0a08686172647761726512422a400a120a05746f74616c12091100000000000014400a080a026f6b120220010a0a0a046e6f6e65120208000a140a0474616773120c320a0a031a01610a031a01625a240a220a08686172647761726512162a140a120a05746f74616c12091100000000000018406203616263100118012019')).items[0];
  assert.equal(event.actor, 'team:x'); assert.equal(event.operation, 'adjust'); assert.equal(event.user_id, 'u1');
  assert.equal(event.project_id, undefined, 'unset optional string stays undefined');
  assert.equal(toIso(event.timestamp), '2026-11-04T15:30:01.500Z');
  assert.deepEqual(event.before, {hardware: {total: 5, ok: true, tags: ['a', 'b'], none: null}});
  assert.deepEqual(event.after, {hardware: {total: 6}});
  assert.equal(event.request_id, 'abc');
});

test('encodes requests exactly like protobuf, including negative int64 and booleans', () => {
  const bytes = encode('hardware.v1.AdjustHardwareRequest', {hardware_id: 'h-é1', delta: -6, expected_version: 2,
    confirmed: true, reason: 'Remove', request_id: 'R-1'});
  assert.equal(toHex(bytes), '0a05682dc3a93110021a0652656d6f766520faffffffffffffffff0128013203522d31');
});

test('encodes nested timestamps and skips unset fields', () => {
  const bytes = encode('hardware.v1.ListTransactionsRequest', {project_id: 'p', page: 3, start: toTimestamp('2026-01-01T00:00:00Z')});
  assert.equal(toHex(bytes), '0a017032060880f2d6ca064003');
});

test('false and zero are sent when set (presence matters)', () => {
  assert.equal(toHex(encode('hardware.v1.AllocateHardwareRequest', {quantity: 0})), '1800');
  assert.equal(toHex(encode('hardware.v1.RetireHardwareRequest', {confirmed: false})), '2000');
});

test('empty response bodies decode to defaults', () => {
  const reply = decode('hardware.v1.SummaryResponse', new Uint8Array());
  assert.deepEqual(reply, {statuses: [], projects: [], project_limit: 0});
});
