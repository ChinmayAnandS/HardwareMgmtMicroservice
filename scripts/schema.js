module.exports = async function(d) {
  const numeric = {bsonType:['int','long','double'],minimum:0,maximum:1000000};
  const existing = new Set(await d.getCollectionNames());
  for(const name of ['hardware','allocations','checkouts','audit','requests','sessions','limits']) {
    if(!existing.has(name)) await d.createCollection(name);
  }
  const validation = await d.runCommand({collMod:'hardware',validator:{$and:[{$jsonSchema:{bsonType:'object',required:['_id','name','asset_tag','total','reserved','out','version','status'],properties:{total:numeric,reserved:numeric,out:numeric,version:{bsonType:['int','long','double'],minimum:1},status:{enum:['active','damaged','under_maintenance','retired']}}}},{$expr:{$and:[{$lte:['$out','$reserved']},{$lte:['$reserved','$total']}]}}]},validationLevel:'strict',validationAction:'error'});
  if(validation.ok !== 1) throw new Error(JSON.stringify(validation));
  await d.hardware.createIndex({asset_key:1},{unique:true});
  await d.hardware.createIndex({name_key:1,_id:1});
  await d.hardware.createIndex({status:1,name_key:1,_id:1});
  await d.hardware.createIndex({category:1,name_key:1,_id:1});
  await d.hardware.createIndex({status:1,_id:1});
  await d.hardware.createIndex({total:1,_id:1});
  await d.allocations.createIndex({hardware_id:1,project_id:1},{unique:true});
  await d.allocations.createIndex({project_id:1,_id:1});
  await d.checkouts.createIndex({hardware_id:1,created_at:-1,_id:-1});
  await d.checkouts.createIndex({project_id:1,created_at:-1,_id:-1});
  await d.checkouts.createIndex({user_id:1,created_at:-1,_id:-1});
  await d.checkouts.createIndex({created_at:-1,_id:-1});
  await d.audit.createIndex({timestamp:-1,_id:-1});
  for(const field of ['hardware_id','project_id','actor','user_id','operation']) await d.audit.createIndex({[field]:1,timestamp:-1,_id:-1});
  await d.audit.createIndex({request_id:1},{unique:true});
  await d.sessions.createIndex({expires_at:1},{expireAfterSeconds:0});
  await d.limits.createIndex({expires_at:1},{expireAfterSeconds:0});
};
