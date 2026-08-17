import { ExternalLink } from 'lucide-react';
import * as React from 'react';

import { isSafeUrl } from '@/lib/schema';
import { cn } from '@/lib/ui';

/**
 * The only way an external URL reaches the DOM. Unsafe URLs are never rendered
 * as links — the package is validated at build time, so this is defence in
 * depth rather than an expected path.
 */
export function SourceLink({
  href,
  children,
  className,
  showIcon = true,
}: {
  href: string | null | undefined;
  children: React.ReactNode;
  className?: string;
  showIcon?: boolean;
}) {
  if (!isSafeUrl(href)) {
    return <span className={cn('text-ink-soft', className)}>{children}</span>;
  }
  return (
    <a
      href={href!}
      target="_blank"
      rel="noopener noreferrer external"
      className={cn(
        'inline-flex items-baseline gap-1 rounded-sm font-medium text-d2 underline decoration-d2/35 underline-offset-2 hover:decoration-d2',
        className,
      )}
    >
      <span className="min-w-0 break-words">{children}</span>
      {showIcon ? (
        <ExternalLink aria-hidden="true" className="size-3 shrink-0 translate-y-0.5" strokeWidth={2.25} />
      ) : null}
      <span className="sr-only"> (opens github.com in a new tab)</span>
    </a>
  );
}
