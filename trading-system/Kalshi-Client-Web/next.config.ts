import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'export',
  basePath: '/client',
  trailingSlash: true,
};

export default nextConfig;
