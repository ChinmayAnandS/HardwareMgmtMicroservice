/* Run with mongosh as a provisioning user; application cannot change schema or roles. */
const fs = require('fs');
const admin = db.getSiblingDB('admin');
admin.auth('root', fs.readFileSync('/run/secrets/mongo_root','utf8').trim());
try { rs.status(); } catch (_) { rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo:27017'}]}); }
let primary = false;
for(let i=0;i<90;i++){ if(admin.hello().isWritablePrimary){primary=true;break;} sleep(1000); }
if(!primary) throw new Error('Replica set did not elect a primary');
const inventory = db.getSiblingDB(process.env.MONGO_DATABASE || 'hardware');
const schema = require('/scripts/schema.js');
schema(inventory);
const privileges = ['hardware','allocations','checkouts'].map(collection=>({resource:{db:inventory.getName(),collection},actions:['find','insert','update']}));
privileges.push(...['audit','requests'].map(collection=>({resource:{db:inventory.getName(),collection},actions:['find','insert']})));
privileges.push(...['sessions','limits'].map(collection=>({resource:{db:inventory.getName(),collection},actions:['find','insert','update','remove']})));
if(!inventory.getRole('hardwareApp')) inventory.createRole({role:'hardwareApp',privileges,roles:[]});
if(!inventory.getUser('hardware_app')) inventory.createUser({user:'hardware_app',pwd:fs.readFileSync('/run/secrets/mongo_app','utf8').trim(),roles:['hardwareApp']});
print('Schema, indexes, and restricted application role are ready.');
