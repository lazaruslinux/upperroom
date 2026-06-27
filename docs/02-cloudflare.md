# 2. Cloudflare: Turnstile and DNS

You need two things from Cloudflare: a Turnstile widget for the bot check, and a
DNS record that points your domain at the server.

## 2.1 Create a Turnstile widget

Turnstile is Cloudflare's bot check. It is free and it replaces the old style
puzzle captchas with a mostly invisible check.

1. Go to https://dash.cloudflare.com and open Turnstile.
2. Add a widget.
3. For the domain, enter your hostname, for example `watch.example.com`.
4. Choose the "Managed" widget type.
5. Save. Cloudflare gives you two values:
   - a sitekey, which is public and goes in the browser
   - a secret key, which is private and stays on the server

Put these into your `.env` later as `TURNSTILE_SITEKEY` and `TURNSTILE_SECRET`.

## 2.2 Point your domain at the server

In the Cloudflare dashboard, open your domain, then DNS, then add a record:

- Type: `A`
- Name: `watch` (this makes `watch.example.com`)
- IPv4 address: `YOUR_SERVER_IP`
- Proxy status: DNS only (grey cloud, not orange)

The grey cloud matters. With it, your video goes straight from the server to the
viewer and never passes through Cloudflare's network. That keeps you clear of
Cloudflare's rules about serving video on the free plan, and it means Caddy can
get its own certificate directly. Turnstile still works fine with the grey
cloud, because the bot check runs as a small script in the browser regardless of
how DNS is set.

The tradeoff is that your server's IP is visible to anyone who looks up the
domain. That is normal for a self hosted service and the firewall plus the login
are what protect you, not hiding the address.

## 2.3 Certificates

You do not need to do anything for HTTPS. When you start the stack, Caddy asks
Let's Encrypt for a certificate for your domain automatically and renews it on
its own. This only works once the DNS record above is pointing at the server, so
set that first.

Continue with `docs/04-run.md` to fill in `.env` and start the stack, or read
`docs/03-obs.md` first to set up OBS.
