/**
 * Steel Claims Cockpit — AppKit backend entry (Wave 7, Stage A, headless).
 *
 * Data access:
 *   - Lakebase (operational OLTP: queue, cockpit, finalize, history) runs as the
 *     App SERVICE PRINCIPAL via the injected identity (appkit.lakebase pool); the
 *     platform mints the DB credential, sslmode=require. No fe-bar profile fallback.
 *   - Governed surfaces use OBO (on-behalf-of the signed-in user): the cockpit
 *     copilot and business chat go through the Genie plugin (OBO), and the business
 *     dashboard's warehouse data through the analytics plugin. OBO scopes are
 *     declared in databricks.yml (dashboards.genie, sql).
 *
 * Authorization is enforced SERVER-SIDE for every /api route by a global guard
 * registered here in onPluginsReady, so it precedes the deferred plugin-route mount
 * and therefore also guards the auto-mounted Genie/analytics routes. Two roles:
 * adjuster and business_user (see authz.ts).
 */

import { createApp, analytics, genie, lakebase, server } from '@databricks/appkit';
import { makeAuthz } from './authz';
import { makeDatabricksRoleResolver } from './identity';
import { registerRoutes } from './routes';

// Operational cockpit space (injected as DATABRICKS_GENIE_SPACE_ID by the
// genie-space app resource) and the reused Wave 10 gold-analytics space.
const OPERATIONAL_GENIE_SPACE = process.env.DATABRICKS_GENIE_SPACE_ID ?? '';
const BUSINESS_GENIE_SPACE = process.env.DATABRICKS_BUSINESS_GENIE_SPACE_ID ?? '01f1bac20bf6119f84fa99c7ba438ba4';

createApp({
  plugins: [
    analytics(),
    genie({
      spaces: {
        // `cockpit` and `history` both resolve to the OPERATIONAL space: the cockpit
        // copilot (adjuster-only) and the Claims-History assistant (both roles) ask
        // claim-level operational questions. They are separate aliases so authz can
        // expose `history` to Business Users WITHOUT widening the adjuster-only cockpit
        // copilot. `business` is the gold-analytics space for the dashboard chat only.
        cockpit: OPERATIONAL_GENIE_SPACE,
        history: OPERATIONAL_GENIE_SPACE,
        business: BUSINESS_GENIE_SPACE,
      },
    }),
    lakebase(),
    server(),
  ],
  onPluginsReady(appkit) {
    // Global server-side authorization. Registered before the deferred plugin-route
    // mount so it guards custom routes AND the Genie/analytics plugin routes.
    const authz = makeAuthz(makeDatabricksRoleResolver());
    appkit.server.extend((app) => {
      // Global (no mount prefix) so req.path is the full path and the guard precedes
      // the deferred plugin-route mount. Default-denies unmapped /api/* (see authz.ts).
      app.use(authz);
    });
    registerRoutes(appkit);
  },
}).catch(console.error);
