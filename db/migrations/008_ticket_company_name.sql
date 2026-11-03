ALTER TABLE tickets ADD COLUMN company_name TEXT NOT NULL DEFAULT '';

UPDATE tickets
SET company_name = CASE category_id
  WHEN 1 THEN 'Alfa Matriz'
  WHEN 2 THEN 'Beta'
  ELSE company_name
END
WHERE COALESCE(company_name, '') = '';
