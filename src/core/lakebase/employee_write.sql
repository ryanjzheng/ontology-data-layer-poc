CREATE TABLE IF NOT EXISTS public.employee_write (
  employee_id TEXT PRIMARY KEY,
  first_name  TEXT,
  department  TEXT,
  hire_date   DATE,
  salary      NUMERIC(12,2),
  status      TEXT,
  is_new      BOOLEAN NOT NULL DEFAULT false,
  is_deleted  BOOLEAN NOT NULL DEFAULT false,
  editor      TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS employee_write_updated_at_idx
  ON public.employee_write (updated_at);
