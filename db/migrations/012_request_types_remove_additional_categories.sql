PRAGMA foreign_keys = ON;

UPDATE tickets
SET category_id = CASE
  WHEN company_name IN ('Alfa Matriz', 'Alfa SPE 1', 'Alfa SPE 2') THEN 1
  WHEN company_name IN ('Beta', 'Beta Filial') THEN 2
  ELSE 1
END
WHERE category_id IN (3, 4);

DELETE FROM request_types
WHERE id IN (3, 4);
