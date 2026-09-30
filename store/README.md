# store

Holds every recording and clip for an upperroom channel, behind a small HTTP
API with two keys. The gate uploads, remuxes, links and deletes with the write
key; Caddy fetches files for viewers with the read key, which can do nothing
else. It never lists a directory over a read, never sees a viewer's cookie, and
answers only names of the one shape the gate writes.

On a single server it runs from the main `docker-compose.yml` beside the gate,
against a local docker volume, and there is nothing to set up beyond the two keys
in `.env`. To keep the archive on another machine, run it there:
`docker-compose.yml` in this directory is an example that puts it behind a
Tailscale sidecar, reachable on one port from your tailnet and nowhere else.

| Variable | Meaning |
|---|---|
| `STORE_DIR` | Where the files live. Default `/media`. |
| `STORE_READ_KEY` | Fetches files. At least 32 characters; `openssl rand -hex 32`. |
| `STORE_WRITE_KEY` | Everything else. At least 32 characters, and not the read key. |
| `STORE_MAX_UPLOAD_BYTES` | The largest single upload. Default 32 GiB. |

The store refuses to start with a key missing, short, or shared between the two.

The API, the security model and the setup guide are in
[`docs/04-run.md`](../docs/04-run.md) and
[`docs/05-security.md`](../docs/05-security.md).
