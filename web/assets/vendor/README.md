# Vendored third-party assets

Self-hosted so the watch page has no third-party CDN dependency (privacy stacks
block those, and this project already self-hosts its fonts for the same reason).

## hls.min.js

- Library: hls.js (HLS playback in browsers)
- Version: 1.6.16
- License: Apache License 2.0
- Source: https://github.com/video-dev/hls.js
- Fetched from: https://cdn.jsdelivr.net/npm/hls.js@1.6.16/dist/hls.min.js

A one-line license/version header comment is kept at the top of the file. To
update, replace the file with a newer 1.x minified build, refresh the version
here, and bump the `?v=` query on the `<script>` tag in web/watch.html.
