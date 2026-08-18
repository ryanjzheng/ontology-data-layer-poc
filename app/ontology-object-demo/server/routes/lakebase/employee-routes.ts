import { randomUUID } from 'node:crypto';

import type { Application, Request } from 'express';
import { z } from 'zod';

interface AppKitWithLakebase {
  lakebase: {
    query(text: string, params?: unknown[]): Promise<{ rows: Record<string, unknown>[] }>;
  };
  server: {
    extend(fn: (app: Application) => void): void;
  };
}

const CreateEmployeeBody = z.object({
  firstName: z.string().trim().min(1).max(120),
  department: z.string().trim().min(1).max(120),
  hireDate: z.iso.date(),
  salary: z.coerce.number().nonnegative().max(9_999_999_999.99).optional(),
  status: z.enum(['active', 'on_leave', 'terminated']).default('active'),
});

const EditEmployeeBody = z
  .object({
    salary: z.coerce.number().nonnegative().max(9_999_999_999.99).optional(),
    status: z.enum(['active', 'on_leave', 'terminated']).optional(),
  })
  .refine((value) => value.salary !== undefined || value.status !== undefined, {
    message: 'Provide salary or status',
  });

const RevertBody = z.object({
  property: z.enum(['salary', 'status']),
});

const employeeSelect = `
  SELECT
    o.employee_id,
    o.first_name,
    o.department,
    o.hire_date,
    o.salary,
    o.status,
    s.salary AS source_salary,
    s.status AS source_status,
    w.salary AS salary_override,
    w.status AS status_override,
    COALESCE(w.is_new, false) AS is_new,
    COALESCE(w.is_deleted, false) AS is_deleted,
    w.editor,
    w.updated_at,
    CASE
      WHEN w.is_new THEN 'app-created'
      WHEN w.employee_id IS NOT NULL THEN 'app-edited'
      ELSE 'source'
    END AS object_origin
  FROM public.employee_overlay o
  LEFT JOIN object_layer.employee_sync s ON s.employee_id = o.employee_id
  LEFT JOIN public.employee_write w ON w.employee_id = o.employee_id
`;

function editorFrom(req: Request): string {
  return req.header('x-forwarded-email') ?? req.header('x-forwarded-user') ?? 'local-demo-user';
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : 'Unexpected Lakebase error';
}

export function setupEmployeeRoutes(appkit: AppKitWithLakebase) {
  appkit.server.extend((app) => {
    app.get('/api/whoami', (req, res) => {
      res.json({ user: editorFrom(req) });
    });

    app.get('/api/employees', async (req, res) => {
      try {
        const search = typeof req.query.search === 'string' ? req.query.search.trim() : '';
        const result = await appkit.lakebase.query(
          `${employeeSelect}
           WHERE ($1 = '' OR o.employee_id ILIKE '%' || $1 || '%'
             OR o.first_name ILIKE '%' || $1 || '%'
             OR o.department ILIKE '%' || $1 || '%')
           ORDER BY o.employee_id
           LIMIT 200`,
          [search]
        );
        res.json(result.rows);
      } catch (error) {
        console.error('Failed to list employees:', error);
        res.status(500).json({ error: message(error) });
      }
    });

    app.get('/api/employees/:employeeId', async (req, res) => {
      try {
        const result = await appkit.lakebase.query(`${employeeSelect} WHERE o.employee_id = $1`, [
          req.params.employeeId,
        ]);
        if (result.rows.length === 0) {
          res.status(404).json({ error: 'Employee not found' });
          return;
        }
        res.json(result.rows[0]);
      } catch (error) {
        console.error('Failed to get employee:', error);
        res.status(500).json({ error: message(error) });
      }
    });

    app.post('/api/employees', async (req, res) => {
      const parsed = CreateEmployeeBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: parsed.error.issues[0]?.message ?? 'Invalid employee' });
        return;
      }

      try {
        const employeeId = `app-${randomUUID()}`;
        await appkit.lakebase.query(
          `INSERT INTO public.employee_write (
             employee_id, first_name, department, hire_date, salary, status,
             is_new, is_deleted, editor, updated_at
           ) VALUES ($1, $2, $3, $4, $5, $6, true, false, $7, now())`,
          [
            employeeId,
            parsed.data.firstName,
            parsed.data.department,
            parsed.data.hireDate,
            parsed.data.salary ?? null,
            parsed.data.status,
            editorFrom(req),
          ]
        );
        const result = await appkit.lakebase.query(`${employeeSelect} WHERE o.employee_id = $1`, [employeeId]);
        res.status(201).json(result.rows[0]);
      } catch (error) {
        console.error('Failed to create employee:', error);
        res.status(500).json({ error: message(error) });
      }
    });

    app.patch('/api/employees/:employeeId', async (req, res) => {
      const parsed = EditEmployeeBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: parsed.error.issues[0]?.message ?? 'Invalid edit' });
        return;
      }

      const columns: string[] = [];
      const values: unknown[] = [req.params.employeeId];
      if (parsed.data.salary !== undefined) {
        values.push(parsed.data.salary);
        columns.push(`salary = $${values.length}`);
      }
      if (parsed.data.status !== undefined) {
        values.push(parsed.data.status);
        columns.push(`status = $${values.length}`);
      }
      values.push(editorFrom(req));
      const editorPosition = values.length;

      try {
        const exists = await appkit.lakebase.query(`SELECT 1 FROM public.employee_overlay WHERE employee_id = $1`, [
          req.params.employeeId,
        ]);
        if (exists.rows.length === 0) {
          res.status(404).json({ error: 'Employee not found' });
          return;
        }
        await appkit.lakebase.query(
          `INSERT INTO public.employee_write (
             employee_id, ${columns.map((entry) => entry.split(' = ')[0]).join(', ')},
             editor, updated_at
           ) VALUES (
             $1, ${columns.map((_, index) => `$${index + 2}`).join(', ')},
             $${editorPosition}, now()
           )
           ON CONFLICT (employee_id) DO UPDATE SET
             ${columns.join(', ')},
             editor = EXCLUDED.editor,
             updated_at = now()`,
          values
        );
        const result = await appkit.lakebase.query(`${employeeSelect} WHERE o.employee_id = $1`, [
          req.params.employeeId,
        ]);
        res.json(result.rows[0]);
      } catch (error) {
        console.error('Failed to edit employee:', error);
        res.status(500).json({ error: message(error) });
      }
    });

    app.post('/api/employees/:employeeId/revert', async (req, res) => {
      const parsed = RevertBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'Property must be salary or status' });
        return;
      }

      try {
        const edit = await appkit.lakebase.query(`SELECT is_new FROM public.employee_write WHERE employee_id = $1`, [
          req.params.employeeId,
        ]);
        if (edit.rows.length === 0) {
          res.status(404).json({ error: 'Employee has no app edit to revert' });
          return;
        }
        if (edit.rows[0]?.is_new) {
          res.status(409).json({ error: 'App-created objects have no source value to restore' });
          return;
        }
        const column = parsed.data.property === 'salary' ? 'salary' : 'status';
        await appkit.lakebase.query(
          `UPDATE public.employee_write
           SET ${column} = NULL, editor = $2, updated_at = now()
           WHERE employee_id = $1`,
          [req.params.employeeId, editorFrom(req)]
        );
        const result = await appkit.lakebase.query(`${employeeSelect} WHERE o.employee_id = $1`, [
          req.params.employeeId,
        ]);
        res.json(result.rows[0]);
      } catch (error) {
        console.error('Failed to revert employee property:', error);
        res.status(500).json({ error: message(error) });
      }
    });

    app.delete('/api/employees/:employeeId', async (req, res) => {
      try {
        const exists = await appkit.lakebase.query(`SELECT 1 FROM public.employee_overlay WHERE employee_id = $1`, [
          req.params.employeeId,
        ]);
        if (exists.rows.length === 0) {
          res.status(404).json({ error: 'Employee not found' });
          return;
        }
        await appkit.lakebase.query(
          `INSERT INTO public.employee_write (
             employee_id, is_new, is_deleted, editor, updated_at
           ) VALUES ($1, false, true, $2, now())
           ON CONFLICT (employee_id) DO UPDATE SET
             is_deleted = true,
             editor = EXCLUDED.editor,
             updated_at = now()`,
          [req.params.employeeId, editorFrom(req)]
        );
        res.status(204).send();
      } catch (error) {
        console.error('Failed to delete employee:', error);
        res.status(500).json({ error: message(error) });
      }
    });
  });
}
