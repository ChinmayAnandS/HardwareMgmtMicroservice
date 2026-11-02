// One-time Atlas setup: creates the hardware collections, validation rules and indexes.
// Run from the repository root (works the same in PowerShell, cmd, macOS and Linux):
//   mongosh "mongodb+srv://<cluster-host>/" --username <atlas-admin-user> --password --file scripts/atlas-schema.js
const path = require('path');

async function setupAtlasSchema() {
  try {
    const applySchema = require(path.resolve('scripts', 'schema.js'));
    const target = db.getSiblingDB('hardware');
    await applySchema(target);
    print('[atlas-schema] OK: ' + (await target.getCollectionNames()).sort().join(', '));
  } catch (error) {
    print('[atlas-schema] FAILED: ' + String(error.message || error));
    quit(1);
  }
}

setupAtlasSchema();