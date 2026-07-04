export const SITE_VERSION =
  process.env.SITE_VERSION ||
  process.env.GITHUB_SHA ||
  new Date().toISOString();
