import { Badge } from '@databricks/appkit-ui/react';
import { Boxes } from 'lucide-react';

import { LakebasePage } from './pages/lakebase/LakebasePage';

export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b bg-card">
        <div className="mx-auto flex w-full max-w-[1500px] items-center justify-between px-4 py-3 md:px-6">
          <div className="flex items-center gap-3">
            <div className="rounded-md bg-primary p-2 text-primary-foreground">
              <Boxes className="h-4 w-4" />
            </div>
            <div>
              <h1 className="text-sm font-semibold tracking-tight">Object Storage Lab</h1>
              <p className="text-xs text-muted-foreground">Employee object type</p>
            </div>
          </div>
          <Badge variant="outline">AppKit · Lakebase</Badge>
        </div>
      </header>
      <main className="px-4 py-6 md:px-6 md:py-8">
        <LakebasePage />
      </main>
    </div>
  );
}
