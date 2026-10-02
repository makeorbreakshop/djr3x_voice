// The Build viewer under starforgeautomation.com/r3x/: the route hands this Worker every request on
// that path, it drops the prefix and serves the published folder (dist-viewer, flat) as static assets.
// A tiny script rather than a prefixed folder, so `publish:viewer` stays one folder that any static
// host can serve at its root too. _headers in the folder applies to the asset paths after the strip.
const PREFIX = '/r3x';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === PREFIX) return Response.redirect(`${url.origin}${PREFIX}/${url.search}${url.hash}`, 301);
    url.pathname = url.pathname.startsWith(PREFIX + '/') ? url.pathname.slice(PREFIX.length) || '/' : url.pathname;
    return env.ASSETS.fetch(new Request(url, request));
  },
};
