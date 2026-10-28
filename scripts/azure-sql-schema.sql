-- Adapt source table/column names to the ADF mappings in your database.
CREATE OR ALTER VIEW dbo.unified_procurement AS
SELECT 'Austin' AS source_unit, vendor, commodity, CAST(NULL AS nvarchar(200)) AS department,
       CAST(NULL AS nvarchar(200)) AS business_line, quantity, unit_price,
       CAST(NULL AS decimal(18,2)) AS payment_amount, CAST(NULL AS decimal(18,2)) AS monthly_charges,
       'Computer' AS category
FROM dbo.austin_procurement
UNION ALL
SELECT 'Dallas', vendor, commodity_description, department, NULL, NULL, NULL,
       payment_amount, NULL, 'Procurement'
FROM dbo.dallas_vendor_payments
UNION ALL
SELECT 'Texas', vendor, NULL, NULL, business_line, NULL, NULL, NULL,
       monthly_charges, 'Cloud'
FROM dbo.texas_cloud_services;

-- Use a dedicated read-only API account and store its password in Key Vault:
-- CREATE USER hardware_api_reader WITH PASSWORD = '<secret>';
-- GRANT SELECT ON OBJECT::dbo.unified_procurement TO hardware_api_reader;
