'use strict';
// These tests exercise asynchronous control flow, not MongoDB server semantics.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const applySchema = require('../schema.js');
const initSource = fs.readFileSync(path.join(__dirname, '../init-db.js'), 'utf8');
const collections = ['hardware', 'allocations', 'checkouts', 'audit', 'requests', 'sessions', 'limits'];

function database(options = {}) {
  const names = new Set(options.existing ? collections : []);
  const calls = [];
  let active = 0;
  async function step(name, result, effect) {
    assert.equal(active, 0, 'Database operations must be awaited in order: ' + name);
    active++;
    await new Promise(resolve => setImmediate(resolve));
    active--;
    calls.push(name);
    if (name === options.failAt) throw new Error('Injected permission failure');
    if (effect) effect();
    return result;
  }
  const d = {
    calls, names,
    getCollectionNames: () => step('getCollectionNames', [...names]),
    createCollection: name => step('create:' + name, {}, () => names.add(name)),
    runCommand: command => step('validation', options.invalidValidation ? {ok: 0, errmsg: 'Invalid validator'} : {ok: 1}),
    getRole: name => step('getRole', options.existingRole ? {role: name} : null),
    createRole: role => step('createRole', {ok: 1}),
    getUser: name => step('getUser', options.existingUser ? {user: name} : null),
    createUser: user => step('createUser', {ok: 1})
  };
  for (const name of collections) {
    d[name] = {createIndex: (keys, settings) => step('index:' + name, 'index')};
  }
  return d;
}

async function runInit(options = {}) {
  const d = database(options);
  const lines = [];
  const adminCalls = [];
  let exitCode;
  let helloCalls = 0;
  const admin = {
    async auth() {
      adminCalls.push('auth');
      if (options.authenticationFails) throw new Error('Authentication failed');
      return {ok: 1};
    },
    async runCommand(command) {
      const type = Object.keys(command)[0];
      adminCalls.push(type);
      if (type === 'replSetGetStatus') {
        if (!options.configured) {
          const e = new Error('Not initialized');
          e.code = 94;
          throw e;
        }
        return {ok: 1, set: options.wrongReplica ? 'other' : 'rs0'};
      }
      if (type === 'replSetInitiate') return {ok: 1};
      if (type === 'hello') return {ok: 1, isWritablePrimary: ++helloCalls > 1, setName: 'rs0'};
      throw new Error('Unexpected admin command');
    }
  };
  const context = vm.createContext({
    require(name) {
      if (name === 'fs') return {readFileSync(file) {
        if (options.missingSecret) throw new Error('Mounted secret file missing');
        return file.endsWith('mongo_root') ? 'test-root-password' : 'test-app-password';
      }};
      if (name === '/scripts/schema.js') return applySchema;
      throw new Error('Unexpected require: ' + name);
    },
    db: {getSiblingDB: name => name === 'admin' ? admin : d},
    process: {env: {MONGO_DATABASE: 'hardware_test'}},
    print: line => lines.push(line),
    quit: code => {exitCode = code;},
    setTimeout: callback => setImmediate(callback)
  });
  await vm.runInContext(initSource, context, {filename: 'init-db.js'});
  return {d, lines, adminCalls, exitCode};
}

test('schema awaits asynchronous collection, validator and index calls', async () => {
  const d = database();
  await applySchema(d);
  assert.equal(d.names.size, 7);
  assert.equal(d.calls.filter(x => x.startsWith('index:')).length, 21);
  assert.ok(d.calls.indexOf('validation') > d.calls.indexOf('create:limits'));
});

test('schema reruns without recreating existing collections', async () => {
  const d = database({existing: true});
  await applySchema(d);
  assert.ok(!d.calls.some(x => x.startsWith('create:')));
  assert.equal(d.calls.filter(x => x.startsWith('index:')).length, 21);
});

test('validator errors stop initialization before indexes', async () => {
  const d = database({invalidValidation: true});
  await assert.rejects(applySchema(d), /Invalid validator/);
  assert.ok(!d.calls.some(x => x.startsWith('index:')));
});

test('index failures propagate before later indexes run', async () => {
  const d = database({failAt: 'index:allocations'});
  await assert.rejects(applySchema(d), /permission failure/);
  assert.ok(!d.calls.includes('index:checkouts'));
});

test('initializer awaits schema before creating role and user', async () => {
  const result = await runInit();
  assert.equal(result.exitCode, 0);
  assert.ok(result.adminCalls.includes('replSetInitiate'));
  assert.ok(result.d.calls.indexOf('createRole') > result.d.calls.indexOf('index:limits'));
  assert.ok(result.d.calls.indexOf('createUser') > result.d.calls.indexOf('createRole'));
  assert.ok(result.lines.includes('[hardware-init] READY: hardware_test'));
  assert.ok(!result.lines.join('\n').includes('test-root-password'));
  assert.ok(!result.lines.join('\n').includes('test-app-password'));
});

test('authentication failures include a readable stage and message', async () => {
  const result = await runInit({authenticationFails: true});
  assert.equal(result.exitCode, 1);
  const line = result.lines.find(x => x.includes('FAILED'));
  assert.match(line, /authenticating the provisioning user/);
  assert.match(line, /Authentication failed/);
  assert.equal(result.d.calls.length, 0);
});

test('schema failure stops user creation and identifies the failed stage', async () => {
  const result = await runInit({failAt: 'index:hardware'});
  assert.equal(result.exitCode, 1);
  assert.ok(!result.d.calls.includes('createUser'));
  assert.match(result.lines.at(-1), /applying collection validation and indexes/);
});

test('existing replica set, role and user can be reused', async () => {
  const result = await runInit({configured: true, existing: true, existingRole: true, existingUser: true});
  assert.equal(result.exitCode, 0);
  assert.ok(!result.adminCalls.includes('replSetInitiate'));
  assert.ok(!result.d.calls.includes('createRole'));
  assert.ok(!result.d.calls.includes('createUser'));
});

test('an unexpected replica-set name is rejected without reinitializing', async () => {
  const result = await runInit({configured: true, wrongReplica: true});
  assert.equal(result.exitCode, 1);
  assert.ok(!result.adminCalls.includes('replSetInitiate'));
  assert.match(result.lines.at(-1), /Expected replica set rs0/);
});

test('missing mounted secret is reported before database authentication', async () => {
  const result = await runInit({missingSecret: true});
  assert.equal(result.exitCode, 1);
  assert.match(result.lines.at(-1), /reading mounted secret files/);
  assert.equal(result.adminCalls.length, 0);
});
