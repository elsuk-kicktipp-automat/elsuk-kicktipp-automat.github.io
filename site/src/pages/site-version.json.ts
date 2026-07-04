import { SITE_VERSION } from '../lib/siteVersion.mjs';

export const prerender = true;

export function GET() {
  return new Response(JSON.stringify({ version: SITE_VERSION }), {
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store, max-age=0',
    },
  });
}
