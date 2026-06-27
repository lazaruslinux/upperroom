# 2. Cloudflare: DNS

You need one thing from Cloudflare: a DNS record that points your domain at the
server.

## 2.1 Point your domain at the server

In the Cloudflare dashboard, open your domain, then DNS, then add a record:

- Type: `A`
- Name: `watch` (this makes `watch.example.com`)
- IPv4 address: `YOUR_SERVER_IP`
- Proxy status: DNS only (grey cloud, not orange)

The grey cloud matters. With it, your video goes straight from the server to the
viewer and never passes through Cloudflare's network. That keeps you clear of
Cloudflare's rules about serving video on the free plan, and it means Caddy can
get its own certificate directly.

The tradeoff is that the VPS's public IP is visible to anyone who looks up the
domain. This is only the rented server's address, never your home IP, which
stays off DNS entirely. That is normal for a self hosted service, and the
firewall plus the login are what protect you, not hiding the address.

## 2.2 Certificates

You do not need to do anything for HTTPS. When you start the stack, Caddy asks
Let's Encrypt for a certificate for your domain automatically and renews it on
its own. This only works once the DNS record above is pointing at the server, so
set that first.

Continue with `docs/04-run.md` to fill in `.env` and start the stack, or read
`docs/03-obs.md` first to set up OBS.
