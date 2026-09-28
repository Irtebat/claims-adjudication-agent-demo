/**
 * Steel Claims Cockpit — app shell.
 *
 * A dense, command-oriented layout: a fixed left rail for navigation and identity, a
 * flush content column. Navigation is role-gated to mirror the server's permission
 * matrix (Adjuster: Work Queue + Claims History; Business User: Business Dashboard +
 * Claims History) — but the server remains the sole enforcement point, so each screen
 * also handles a 403 defensively. The Claim Cockpit is a near-full-screen modal opened
 * from the Queue and History, not a nav destination.
 */

import { createBrowserRouter, RouterProvider, NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router';
import { useEffect, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  TooltipProvider,
} from '@databricks/appkit-ui/react';
import { ClipboardList, History, LayoutDashboard, Menu, PanelsTopLeft } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Role } from '@/lib/types';
import { WhoamiProvider } from '@/components/RoleContext';
import { useActiveRole, useWhoami } from '@/components/whoami';
import { PersonaSwitch } from '@/components/PersonaSwitch';
import { EmptyState, ErrorState, LoadingPanel } from '@/components/States';
import { WorkQueuePage } from '@/pages/queue/WorkQueuePage';
import { ClaimsHistoryPage } from '@/pages/history/ClaimsHistoryPage';
import { BusinessDashboardPage } from '@/pages/dashboard/BusinessDashboardPage';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  roles: Role[];
}

const NAV: NavItem[] = [
  { to: '/queue', label: 'Work Queue', icon: ClipboardList, roles: ['adjuster'] },
  { to: '/dashboard', label: 'Business Dashboard', icon: LayoutDashboard, roles: ['business_user'] },
  { to: '/history', label: 'Claims History', icon: History, roles: ['adjuster', 'business_user'] },
];

const ROLE_LABEL: Record<Role, string> = { adjuster: 'Adjuster', business_user: 'Business User' };
const HOME_FOR: Record<Role, string> = { adjuster: '/queue', business_user: '/dashboard' };

function navItemsFor(role: Role | null): NavItem[] {
  if (!role) return [];
  return NAV.filter((n) => n.roles.includes(role));
}

function NavList({ role, onNavigate }: { role: Role | null; onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Primary">
      {navItemsFor(role).map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              isActive
                ? 'bg-secondary text-foreground'
                : 'text-muted-foreground hover:bg-secondary/60 hover:text-foreground'
            )
          }
        >
          {({ isActive }) => (
            <>
              <span
                aria-hidden
                className={cn('h-4 w-0.5 rounded-full', isActive ? 'bg-[var(--db-lava)]' : 'bg-transparent')}
              />
              <Icon className="h-4 w-4 shrink-0" aria-hidden />
              {label}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground">
        <PanelsTopLeft className="h-4 w-4" aria-hidden />
      </span>
      <span className="text-sm font-bold leading-tight tracking-tight text-foreground">
        Steel Claims
        <br />
        Cockpit
      </span>
    </div>
  );
}

function Identity() {
  const { data, roles, activeRole } = useWhoami();
  const email = data?.email ?? data?.user ?? 'Unknown user';
  // A multi-role caller sees the persona switch (rendered above) instead of a static
  // badge; a single-role caller gets the badge as a plain, non-interactive indicator.
  const soleRole = roles.length === 1 ? activeRole : null;
  return (
    <div className="flex flex-col gap-1.5">
      {soleRole && (
        <Badge variant="outline" className="w-fit font-medium">
          {ROLE_LABEL[soleRole]}
        </Badge>
      )}
      <span className="truncate text-xs text-muted-foreground" title={email}>
        {email}
      </span>
    </div>
  );
}

function Layout() {
  const [navOpen, setNavOpen] = useState(false);
  const { loading, unauthorized, error, reload, activeRole } = useWhoami();
  const navigate = useNavigate();
  const role = activeRole;

  // On a genuine persona switch (a change between two real roles), land on the new
  // persona's home so the visible surface matches the chosen view. The initial
  // null->role seed is skipped so a deep link survives the first load.
  const prevRole = useRef<Role | null>(null);
  useEffect(() => {
    const prev = prevRole.current;
    prevRole.current = role;
    if (prev && role && prev !== role) void navigate(HOME_FOR[role]);
  }, [role, navigate]);

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-background p-8">
        <LoadingPanel className="w-full max-w-sm" lines={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="grid min-h-screen place-items-center bg-background p-8">
        <ErrorState className="max-w-md" title="Couldn’t sign you in" message={error} onRetry={reload} />
      </div>
    );
  }

  if (unauthorized) {
    return (
      <div className="grid min-h-screen place-items-center bg-background p-8">
        <EmptyState
          className="max-w-md"
          title="No access"
          message="Your account isn’t assigned to the Adjuster or Business User group for this application. Contact your workspace administrator to request access."
        />
      </div>
    );
  }

  const rail = (
    <div className="flex h-full flex-col gap-6 p-4">
      <Brand />
      <NavList role={role} onNavigate={() => setNavOpen(false)} />
      <div className="mt-auto flex flex-col gap-3 border-t border-sidebar-border pt-4">
        {/* Persona switch: only shown for a caller holding both roles. */}
        <PersonaSwitch />
        <Identity />
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-background">
      {/* Fixed left rail on desktop */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-60 border-r border-sidebar-border bg-sidebar md:block">
        {rail}
      </aside>

      {/* Mobile top bar + drawer */}
      <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-border bg-sidebar px-4 py-2.5 md:hidden">
        <Sheet open={navOpen} onOpenChange={setNavOpen}>
          <Button variant="ghost" size="icon" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            <Menu className="h-5 w-5" />
          </Button>
          <SheetContent side="left" className="w-64 p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation</SheetTitle>
            </SheetHeader>
            {rail}
          </SheetContent>
        </Sheet>
        <Brand />
      </header>

      <main className="md:pl-60">
        <div className="mx-auto w-full max-w-[1600px] px-4 py-5 md:px-8 md:py-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

/** Redirect the index route to the active persona's home surface. */
function RootRedirect() {
  const role = useActiveRole();
  if (!role) return <Navigate to="/history" replace />;
  return <Navigate to={HOME_FOR[role]} replace />;
}

/**
 * Client-side role gate for direct-URL access, keyed off the ACTIVE persona so the
 * visible surfaces match the chosen view. The server still enforces per route, so this
 * is a UX convenience, not the control — a caller who also holds the required role can
 * reach the surface by switching persona.
 */
function RequireRole({ allow, children }: { allow: Role[]; children: React.ReactNode }) {
  const location = useLocation();
  const role = useActiveRole();
  if (role && !allow.includes(role)) {
    return (
      <EmptyState
        title="Not available for your role"
        message="This surface is restricted. Use the navigation to return to a page you can access."
        action={
          <Button asChild variant="outline" size="sm">
            <NavLink to={HOME_FOR[role]}>Go to {ROLE_LABEL[role]} home</NavLink>
          </Button>
        }
      />
    );
  }
  // key on pathname so a role-allowed page still remounts per route
  return <div key={location.pathname}>{children}</div>;
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { index: true, element: <RootRedirect /> },
      {
        path: '/queue',
        element: (
          <RequireRole allow={['adjuster']}>
            <WorkQueuePage />
          </RequireRole>
        ),
      },
      {
        path: '/dashboard',
        element: (
          <RequireRole allow={['business_user']}>
            <BusinessDashboardPage />
          </RequireRole>
        ),
      },
      {
        path: '/history',
        element: (
          <RequireRole allow={['adjuster', 'business_user']}>
            <ClaimsHistoryPage />
          </RequireRole>
        ),
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);

export default function App() {
  return (
    <WhoamiProvider>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </WhoamiProvider>
  );
}
