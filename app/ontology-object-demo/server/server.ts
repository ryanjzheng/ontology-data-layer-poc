import { createApp, lakebase, server } from '@databricks/appkit';
import { setupEmployeeRoutes } from './routes/lakebase/employee-routes';

createApp({
  plugins: [lakebase(), server()],
  onPluginsReady(appkit) {
    setupEmployeeRoutes(appkit);
  },
}).catch(console.error);
