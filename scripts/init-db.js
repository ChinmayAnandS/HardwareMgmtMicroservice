/* Executed by mongosh. Credentials remain in mounted Docker secret files. */
async function initializeHardwareDatabase() {
  let stage = 'reading mounted secret files';
  const report = value => {
    stage = value;
    print('[hardware-init] ' + value);
  };

  try {
    report(stage);
    const fileSystem = require('fs');
    const rootPassword = fileSystem.readFileSync('/run/secrets/mongo_root', 'utf8').trim();
    const appPassword = fileSystem.readFileSync('/run/secrets/mongo_app', 'utf8').trim();
    if (!rootPassword || !appPassword) throw new Error('A mounted password file is empty');

    const adminDb = db.getSiblingDB('admin');
    report('authenticating the provisioning user');
    const authenticated = await adminDb.auth('root', rootPassword);
    if (authenticated === false || authenticated === 0 || authenticated?.ok === 0) {
      throw new Error('Provisioning authentication failed');
    }

    report('checking replica-set configuration');
    try {
      const status = await adminDb.runCommand({replSetGetStatus: 1});
      if (status.ok !== 1) {
        const error = new Error(status.errmsg || 'Cannot read replica-set status');
        error.code = status.code;
        throw error;
      }
      if (status.set !== 'rs0') throw new Error('Expected replica set rs0');
    } catch (error) {
      if (error.code !== 94 && error.codeName !== 'NotYetInitialized') throw error;
      report('initializing replica set rs0');
      const result = await adminDb.runCommand({
        replSetInitiate: {_id: 'rs0', members: [{_id: 0, host: 'mongo:27017'}]}
      });
      if (result.ok !== 1) throw new Error(result.errmsg || 'Replica-set initialization failed');
    }

    report('waiting for a writable primary');
    let primary = false;
    for (let attempt = 0; attempt < 90; attempt++) {
      const hello = await adminDb.runCommand({hello: 1});
      if (hello.isWritablePrimary && hello.setName === 'rs0') {
        primary = true;
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    if (!primary) throw new Error('Replica set did not elect a primary within 90 seconds');

    const databaseName = process.env.MONGO_DATABASE || 'hardware';
    const inventory = db.getSiblingDB(databaseName);
    report('applying collection validation and indexes to ' + databaseName);
    const applySchema = require('/scripts/schema.js');
    await applySchema(inventory);

    report('ensuring the restricted application role in ' + databaseName);
    const privileges = ['hardware', 'allocations', 'checkouts'].map(collection => ({
      resource: {db: databaseName, collection}, actions: ['find', 'insert', 'update']
    }));
    privileges.push(...['audit', 'requests'].map(collection => ({
      resource: {db: databaseName, collection}, actions: ['find', 'insert']
    })));
    privileges.push(...['sessions', 'limits'].map(collection => ({
      resource: {db: databaseName, collection}, actions: ['find', 'insert', 'update', 'remove']
    })));
    if (!(await inventory.getRole('hardwareApp'))) {
      await inventory.createRole({role: 'hardwareApp', privileges, roles: []});
    }

    report('ensuring the application user in ' + databaseName);
    if (!(await inventory.getUser('hardware_app'))) {
      await inventory.createUser({user: 'hardware_app', pwd: appPassword, roles: ['hardwareApp']});
    }
    print('[hardware-init] READY: ' + databaseName);
    quit(0);
  } catch (error) {
    // Avoid relying on mongosh's stack formatter; never print credential contents.
    print('[hardware-init] FAILED ' + JSON.stringify({
      stage, name: error.name || 'Error', code: error.code ?? null,
      message: String(error.message || error)
    }));
    quit(1);
  }
}

initializeHardwareDatabase();
