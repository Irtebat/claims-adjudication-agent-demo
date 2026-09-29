/**
 * PersonaSwitch — the in-app persona toggle for a caller who holds BOTH roles.
 *
 * Rendered ONLY when the user has more than one role (a single-role user sees nothing
 * here — their role is shown as a badge in the rail instead). Switching changes the
 * ACTIVE persona, which reshapes navigation, the home surface, and which endpoints the
 * UI calls. It is a VIEW preference only: the server authorizes every request against
 * the caller's full server-resolved role set, so this control can never widen access.
 *
 * A compact segmented control composed from primitives (AppKit ships no segmented persona
 * toggle): a recessed neutral track with a raised (lighter) active segment, keeping the
 * indigo accent for the nav's active-item rail rather than spending it here. Handles
 * hover / active / focus-visible; each segment is a real button with `aria-pressed`,
 * grouped under a labelled region for assistive tech.
 */

import { ClipboardList, LayoutDashboard } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Role } from '@/lib/types';
import { useActiveRole, useRoles, useSetActiveRole } from './whoami';

interface Persona {
  role: Role;
  label: string;
  icon: LucideIcon;
}

// Order is stable (adjuster first) so the control doesn't reflow between users. Icons
// mirror each persona's home nav item (Work Queue / Business Dashboard).
const PERSONAS: Persona[] = [
  { role: 'adjuster', label: 'Adjuster', icon: ClipboardList },
  { role: 'business_user', label: 'Business', icon: LayoutDashboard },
];

export function PersonaSwitch({ className }: { className?: string }) {
  const roles = useRoles();
  const activeRole = useActiveRole();
  const setActiveRole = useSetActiveRole();

  // Only a multi-role caller gets the switch; a single-role user has nothing to toggle.
  if (roles.length < 2) return null;
  const choices = PERSONAS.filter((p) => roles.includes(p.role));

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <span
        id="persona-switch-label"
        className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground"
      >
        View as
      </span>
      <div
        role="group"
        aria-labelledby="persona-switch-label"
        className="inline-flex rounded-md border border-border bg-muted p-0.5"
      >
        {choices.map(({ role, label, icon: Icon }) => {
          const active = activeRole === role;
          return (
            <button
              key={role}
              type="button"
              aria-pressed={active}
              onClick={() => setActiveRole(role)}
              className={cn(
                'inline-flex flex-1 items-center justify-center gap-1.5 rounded-[0.3rem] px-2.5 py-1.5 text-xs font-medium transition-colors duration-100',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-sidebar',
                active ? 'bg-secondary text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
