import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@databricks/appkit-ui/react';
import {
  ArrowRight,
  Database,
  PencilLine,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  UserRoundPlus,
} from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

interface Employee {
  employee_id: string;
  first_name: string;
  department: string;
  hire_date: string;
  salary: string | null;
  status: string | null;
  source_salary: string | null;
  source_status: string | null;
  salary_override: string | null;
  status_override: string | null;
  is_new: boolean;
  is_deleted: boolean;
  editor: string | null;
  updated_at: string | null;
  object_origin: 'source' | 'app-edited' | 'app-created';
}

interface CreateForm {
  firstName: string;
  department: string;
  hireDate: string;
  salary: string;
  status: string;
}

const emptyCreateForm: CreateForm = {
  firstName: '',
  department: '',
  hireDate: new Date().toISOString().slice(0, 10),
  salary: '',
  status: 'active',
};

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

function formatSalary(value: string | null): string {
  if (value === null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function originBadge(origin: Employee['object_origin']) {
  if (origin === 'app-created') return <Badge>App-created</Badge>;
  if (origin === 'app-edited') return <Badge variant="secondary">App-edited</Badge>;
  return <Badge variant="outline">Source</Badge>;
}

function FlowCard({
  icon: Icon,
  title,
  detail,
  caption,
}: {
  icon: typeof Database;
  title: string;
  detail: string;
  caption: string;
}) {
  return (
    <Card className="min-w-0 flex-1">
      <CardContent className="p-4">
        <div className="mb-3 flex items-center gap-2">
          <div className="rounded-md bg-primary/10 p-2 text-primary">
            <Icon className="h-4 w-4" />
          </div>
          <p className="text-sm font-semibold">{title}</p>
        </div>
        <p className="text-lg font-semibold tracking-tight">{detail}</p>
        <p className="mt-1 text-xs text-muted-foreground">{caption}</p>
      </CardContent>
    </Card>
  );
}

export function LakebasePage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Employee | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(emptyCreateForm);
  const [editSalary, setEditSalary] = useState('');
  const [editStatus, setEditStatus] = useState('active');

  const loadEmployees = useCallback(async (term: string) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await api<Employee[]>(`/api/employees?search=${encodeURIComponent(term.trim())}`);
      setEmployees(rows);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not load employees');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadEmployees('');
  }, [loadEmployees]);

  const metrics = useMemo(
    () => ({
      effective: employees.length,
      edited: employees.filter((employee) => employee.object_origin === 'app-edited').length,
      created: employees.filter((employee) => employee.is_new).length,
      highRisk: employees.filter((employee) => Number(employee.salary ?? 0) > 200_000).length,
    }),
    [employees]
  );

  const openEdit = (employee: Employee) => {
    setSelected(employee);
    setEditSalary(employee.salary ?? '');
    setEditStatus(employee.status ?? 'active');
    setError(null);
  };

  const refreshSelected = async (employeeId: string) => {
    const employee = await api<Employee>(`/api/employees/${encodeURIComponent(employeeId)}`);
    setSelected(employee);
    setEmployees((current) => current.map((item) => (item.employee_id === employee.employee_id ? employee : item)));
  };

  const submitEdit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api<Employee>(`/api/employees/${encodeURIComponent(selected.employee_id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ salary: editSalary, status: editStatus }),
      });
      setSelected(updated);
      setEmployees((current) => current.map((item) => (item.employee_id === updated.employee_id ? updated : item)));
      setNotice(`Saved user edit for ${updated.employee_id}. Lakebase overlay updated immediately.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save edit');
    } finally {
      setSaving(false);
    }
  };

  const revertProperty = async (property: 'salary' | 'status') => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await api<Employee>(`/api/employees/${encodeURIComponent(selected.employee_id)}/revert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ property }),
      });
      await refreshSelected(selected.employee_id);
      setNotice(`Reverted ${property}; the effective value now falls back to upstream source.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Could not revert ${property}`);
    } finally {
      setSaving(false);
    }
  };

  const deleteEmployee = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await api<void>(`/api/employees/${encodeURIComponent(selected.employee_id)}`, {
        method: 'DELETE',
      });
      setEmployees((current) => current.filter((item) => item.employee_id !== selected.employee_id));
      setNotice(`Wrote tombstone for ${selected.employee_id}; BASE remains unchanged.`);
      setSelected(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not delete employee');
    } finally {
      setSaving(false);
    }
  };

  const createEmployee = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await api<Employee>('/api/employees', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...createForm,
          salary: createForm.salary || undefined,
        }),
      });
      setEmployees((current) => [created, ...current]);
      setNotice(`Created ${created.employee_id} in the app edit store—no BASE row was written.`);
      setCreateForm(emptyCreateForm);
      setCreateOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not create employee');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-6">
      <section className="flex flex-col gap-4 border-b pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-primary">
            <ShieldCheck className="h-4 w-4" />
            Object Storage behavioral reconstruction
          </div>
          <h2 className="text-3xl font-semibold tracking-tight md:text-4xl">
            Source truth stays still. User intent moves.
          </h2>
          <p className="mt-3 text-sm leading-6 text-muted-foreground md:text-base">
            Inspect the effective employee object, apply user edits, or create a new object. Every write lands in
            Lakebase <code>employee_write</code>—never in UC BASE.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Create object
        </Button>
      </section>

      <section aria-label="Object reconciliation flow" className="flex flex-col gap-3 lg:flex-row">
        <FlowCard
          icon={Database}
          title="1 · Upstream source"
          detail={`${metrics.effective - metrics.created} synced objects`}
          caption="UC BASE → read-only Lakebase mirror"
        />
        <ArrowRight className="hidden h-5 w-5 self-center text-muted-foreground lg:block" />
        <FlowCard
          icon={PencilLine}
          title="2 · User edit store"
          detail={`${metrics.edited} overrides · ${metrics.created} created`}
          caption="Sparse edits, app-created rows, tombstones"
        />
        <ArrowRight className="hidden h-5 w-5 self-center text-muted-foreground lg:block" />
        <FlowCard
          icon={ShieldCheck}
          title="3 · Effective object"
          detail={`${metrics.effective} visible · ${metrics.highRisk} high risk`}
          caption="Per-property reconciliation, edits win"
        />
      </section>

      {notice && (
        <Alert>
          <ShieldCheck className="h-4 w-4" />
          <AlertTitle>Object state changed</AlertTitle>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Action failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader className="gap-4 border-b md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Effective employee objects</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Lakebase overlay · immediate after app writes · up to 200 objects
            </p>
          </div>
          <form
            className="flex w-full gap-2 md:w-auto"
            onSubmit={(event) => {
              event.preventDefault();
              void loadEmployees(search);
            }}
          >
            <div className="relative min-w-0 flex-1 md:w-72">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                id="employee-search"
                name="employee-search"
                aria-label="Search employees"
                className="pl-9"
                placeholder="ID, name, or department"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <Button type="submit" variant="outline" aria-label="Refresh employees">
              <RefreshCw className="h-4 w-4" />
            </Button>
          </form>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-3 p-6">
              {Array.from({ length: 6 }, (_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </div>
          ) : employees.length === 0 ? (
            <div className="p-12 text-center">
              <Database className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
              <p className="font-medium">No effective objects found</p>
              <p className="mt-1 text-sm text-muted-foreground">Clear the search or create an app-owned employee.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Object</TableHead>
                    <TableHead>Department</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Effective salary</TableHead>
                    <TableHead>Origin</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {employees.map((employee) => (
                    <TableRow key={employee.employee_id}>
                      <TableCell>
                        <p className="font-medium">{employee.first_name}</p>
                        <p className="font-mono text-xs text-muted-foreground">{employee.employee_id}</p>
                      </TableCell>
                      <TableCell>{employee.department}</TableCell>
                      <TableCell>{employee.status ?? '—'}</TableCell>
                      <TableCell className="text-right font-mono">{formatSalary(employee.salary)}</TableCell>
                      <TableCell>{originBadge(employee.object_origin)}</TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="sm" onClick={() => openEdit(employee)}>
                          Inspect
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle>{selected.first_name}</DialogTitle>
                <DialogDescription>
                  {selected.employee_id} · {selected.department} · {originBadge(selected.object_origin)}
                </DialogDescription>
              </DialogHeader>

              <div className="grid gap-3 sm:grid-cols-3">
                <Card>
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Source</p>
                    <p className="mt-2 font-mono text-sm">{formatSalary(selected.source_salary)}</p>
                    <p className="text-xs text-muted-foreground">{selected.source_status ?? '—'}</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">App override</p>
                    <p className="mt-2 font-mono text-sm">{formatSalary(selected.salary_override)}</p>
                    <p className="text-xs text-muted-foreground">{selected.status_override ?? 'No status override'}</p>
                  </CardContent>
                </Card>
                <Card className="border-primary/40">
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-primary">Effective</p>
                    <p className="mt-2 font-mono text-sm">{formatSalary(selected.salary)}</p>
                    <p className="text-xs text-muted-foreground">{selected.status ?? '—'}</p>
                  </CardContent>
                </Card>
              </div>

              <Separator />

              <form className="space-y-4" onSubmit={(event) => void submitEdit(event)}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="edit-salary">Salary override</Label>
                    <Input
                      id="edit-salary"
                      name="salary"
                      inputMode="decimal"
                      value={editSalary}
                      onChange={(event) => setEditSalary(event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="edit-status">Status override</Label>
                    <Select name="status" value={editStatus} onValueChange={setEditStatus}>
                      <SelectTrigger id="edit-status">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="active">Active</SelectItem>
                        <SelectItem value="on_leave">On leave</SelectItem>
                        <SelectItem value="terminated">Terminated</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={saving || selected.is_new || selected.salary_override === null}
                    onClick={() => void revertProperty('salary')}
                  >
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Revert salary
                  </Button>
                  <Button type="submit" disabled={saving}>
                    {saving ? 'Saving…' : 'Save user edit'}
                  </Button>
                </DialogFooter>
              </form>

              <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-muted-foreground">
                  Last app action: {selected.editor ?? 'none'}
                  {selected.updated_at ? ` · ${new Date(selected.updated_at).toLocaleString()}` : ''}
                </p>
                <Button variant="destructive" disabled={saving} onClick={() => void deleteEmployee()}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Write tombstone
                </Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Create app-owned object</DialogTitle>
            <DialogDescription>
              Generates an <code>app-*</code> ID and writes the complete object only to Lakebase{' '}
              <code>employee_write</code>.
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={(event) => void createEmployee(event)}>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="create-name">Name</Label>
                <Input
                  id="create-name"
                  name="firstName"
                  required
                  value={createForm.firstName}
                  onChange={(event) => setCreateForm((current) => ({ ...current, firstName: event.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-department">Department</Label>
                <Input
                  id="create-department"
                  name="department"
                  required
                  value={createForm.department}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      department: event.target.value,
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-hire-date">Hire date</Label>
                <Input
                  id="create-hire-date"
                  name="hireDate"
                  required
                  type="date"
                  value={createForm.hireDate}
                  onChange={(event) => setCreateForm((current) => ({ ...current, hireDate: event.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-salary">Salary</Label>
                <Input
                  id="create-salary"
                  name="salary"
                  inputMode="decimal"
                  value={createForm.salary}
                  onChange={(event) => setCreateForm((current) => ({ ...current, salary: event.target.value }))}
                />
              </div>
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="create-status">Status</Label>
                <Select
                  name="status"
                  value={createForm.status}
                  onValueChange={(status) => setCreateForm((current) => ({ ...current, status }))}
                >
                  <SelectTrigger id="create-status">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="on_leave">On leave</SelectItem>
                    <SelectItem value="terminated">Terminated</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saving}>
                <UserRoundPlus className="mr-2 h-4 w-4" />
                {saving ? 'Creating…' : 'Create object'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
