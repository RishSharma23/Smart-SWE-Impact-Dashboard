/** @type {import('next').NextConfig} */

// GitHub Pages serves project sites from /<repo>. Set NEXT_PUBLIC_BASE_PATH at
// build time; Cloudflare Pages / Vercel serve from the root and leave it empty.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

const nextConfig = {
  output: 'export',
  basePath: basePath || undefined,
  trailingSlash: true,
  reactStrictMode: true,
  // Static export has no image optimizer; avatars are plain <img> with explicit
  // dimensions (see SourceAvatar) so there is no layout shift.
  images: { unoptimized: true },
  productionBrowserSourceMaps: false,
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
