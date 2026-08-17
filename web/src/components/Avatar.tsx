'use client';

import * as React from 'react';

import { cn } from '@/lib/ui';

/**
 * A GitHub avatar with explicit dimensions (no layout shift) and a neutral
 * fallback when the image fails.
 *
 * Contract §12: the fallback is deliberately a plain monogram tile, not a
 * generated identicon — an identicon could be mistaken for a real photograph of
 * a real person.
 */
export function Avatar({
  src,
  login,
  size = 48,
  className,
}: {
  src: string | null | undefined;
  login: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = React.useState(false);
  const initial = (login || '?').replace(/^@/, '').charAt(0).toUpperCase();

  const style = { width: size, height: size } as const;

  if (!src || failed) {
    return (
      <span
        aria-hidden="true"
        style={style}
        className={cn(
          'grid shrink-0 place-items-center rounded-full border border-line bg-surface-sunken font-mono font-semibold text-muted',
          className,
        )}
      >
        <span style={{ fontSize: Math.round(size * 0.4) }}>{initial}</span>
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      style={style}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={cn('shrink-0 rounded-full border border-line bg-surface-sunken object-cover', className)}
    />
  );
}
