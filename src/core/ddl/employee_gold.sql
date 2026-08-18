CREATE OR REPLACE VIEW ${catalog}.${schema}.employee_gold AS
SELECT
  COALESCE(a.employee_id, b.employee_id) AS employee_id,
  COALESCE(a.first_name,  b.first_name)  AS first_name,
  COALESCE(a.department,  b.department)  AS department,
  COALESCE(a.hire_date,   b.hire_date)   AS hire_date,
  COALESCE(a.salary,      b.salary)      AS salary,
  COALESCE(a.status,      b.status)      AS status,
  (COALESCE(a.salary, b.salary) > 200000) AS is_high_risk
FROM ${catalog}.${schema}.employee_base b
FULL OUTER JOIN ${catalog}.${schema}.employee_app_changes a
  ON b.employee_id = a.employee_id
WHERE COALESCE(a.is_deleted, false) = false;
