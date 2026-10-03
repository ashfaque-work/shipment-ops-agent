/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone server bundle only for the Docker image (set in docker/frontend.Dockerfile);
  // `next start` (local, tests) warns if it is on.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  reactStrictMode: true,
};

export default nextConfig;
