/* Run with mongosh to prepare an empty database: MONGO_DATABASE=name mongosh "$MONGO_URI" --file apply-schema.js
   Used by the dev pipeline to give every preview its own database with the production collections and indexes. */
(async () => {
  const name = process.env.MONGO_DATABASE;
  if (!name) throw new Error('MONGO_DATABASE is required');
  await require('/scripts/schema.js')(db.getSiblingDB(name));
  print('[apply-schema] schema applied to ' + name);
})();
