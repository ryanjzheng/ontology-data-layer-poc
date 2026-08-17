CREATE OR REPLACE VIEW public.employee_overlay AS
SELECT
  COALESCE(w.employee_id, s.employee_id) AS employee_id,
  COALESCE(w.first_name,  s.first_name)  AS first_name,
  COALESCE(w.department,  s.department)  AS department,
  COALESCE(w.hire_date,   s.hire_date)   AS hire_date,
  COALESCE(w.salary,      s.salary)      AS salary,
  COALESCE(w.status,      s.status)      AS status
FROM object_layer.employee_sync s
FULL OUTER JOIN public.employee_write w ON s.employee_id = w.employee_id
WHERE COALESCE(w.is_deleted, false) = false;
