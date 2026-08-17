import * as React from 'react';

import { cn, titleize } from '@/lib/ui';

import { DataTable, TableScroll, Td, Th } from './primitives';

/**
 * Renders an arbitrary slice of methodology.json as readable, structured
 * chrome — labels, enums and numbers, never prose about a person. Phase 2 may
 * add configuration keys without this needing a change.
 */
export function JsonSpec({ value, depth = 0, className }: { value: unknown; depth?: number; className?: string }) {
  if (value === null || value === undefined) {
    return <span className="italic text-unknown">not set</span>;
  }
  if (typeof value === 'boolean') {
    return <span className="font-mono text-ink">{String(value)}</span>;
  }
  if (typeof value === 'number') {
    return <span className="font-mono text-ink">{value}</span>;
  }
  if (typeof value === 'string') {
    return <span className={value.length > 90 ? 'text-ink-soft' : 'text-ink'}>{value}</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="italic text-unknown">empty</span>;
    const allScalar = value.every((v) => typeof v !== 'object' || v === null);
    if (allScalar) {
      return (
        <ul className={cn('flex flex-wrap gap-1.5', className)}>
          {value.map((v, i) => (
            <li
              key={i}
              className="rounded border border-line-strong bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-soft"
            >
              {String(v)}
            </li>
          ))}
        </ul>
      );
    }
    // Array of objects with consistent keys renders as a table.
    const keys = [...new Set(value.flatMap((v) => Object.keys(v as object)))];
    if (keys.length <= 8 && value.length <= 60) {
      return (
        <TableScroll label="Configuration table">
          <DataTable>
            <thead>
              <tr>
                {keys.map((k) => (
                  <Th key={k}>{k.replace(/_/g, ' ')}</Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {value.map((row, i) => (
                <tr key={i}>
                  {keys.map((k) => (
                    <Td key={k} className="max-w-[22rem] text-xs">
                      <JsonSpec value={(row as Record<string, unknown>)[k]} depth={depth + 1} />
                    </Td>
                  ))}
                </tr>
              ))}
            </tbody>
          </DataTable>
        </TableScroll>
      );
    }
    return (
      <ol className={cn('space-y-2', className)}>
        {value.map((v, i) => (
          <li key={i} className="rounded border border-line bg-surface p-2">
            <JsonSpec value={v} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }

  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="italic text-unknown">empty</span>;

  return (
    <dl className={cn(depth === 0 ? 'space-y-4' : 'space-y-2', className)}>
      {entries.map(([key, v]) => {
        const nested = typeof v === 'object' && v !== null;
        return (
          <div
            key={key}
            className={cn(nested && depth < 2 ? 'rounded-lg border border-line bg-surface/70 p-3' : '', 'min-w-0')}
          >
            <dt
              className={cn(
                depth === 0
                  ? 'text-sm font-semibold text-ink'
                  : 'font-mono text-[11px] uppercase tracking-[0.1em] text-muted',
              )}
            >
              {depth === 0 ? titleize(key) : key.replace(/_/g, ' ')}
            </dt>
            <dd className={cn('min-w-0 text-sm leading-relaxed', depth === 0 ? 'mt-2' : 'mt-0.5 text-ink-soft')}>
              <JsonSpec value={v} depth={depth + 1} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
