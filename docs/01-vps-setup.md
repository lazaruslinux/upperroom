# 1. Prepare the server

upperroom runs on any small Linux server with a public IP. A cheap virtual
server with one CPU and 1 GB of memory is enough for a single viewer, since the
server only repackages video and never transcodes it. These steps use Debian or
Ubuntu, which is what most providers offer by default.

Throughout these docs, replace the placeholders with your own values:

- `YOUR_SERVER_IP` is the public IP of the server.
- `YOUR_HOME_IP` is your home internet IP. Find it by searching "what is my ip"
  from your home network.
- `watch.example.com` is the domain you will use.

## 1.1 Connect

```
ssh root@YOUR_SERVER_IP
```

## 1.2 Update and install Docker

```
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
```

That script installs Docker and the compose plugin. Check it:

```
docker --version
docker compose version
```

## 1.3 Lock down SSH

Password logins are the most common way servers get broken into. If you signed
in with an SSH key already, turn passwords off. Edit
`/etc/ssh/sshd_config` and set:

```
PasswordAuthentication no
PermitRootLogin prohibit-password
```

Then reload SSH:

```
systemctl restart ssh
```

Keep your current session open and test a new one in a second terminal before
you trust it.

## 1.4 Firewall

We allow only what we need. The web ports are open to everyone, because viewers
come from anywhere. The RTMP ingest port is open only to your home IP, because
you are the only one who publishes.

```
apt install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow from YOUR_HOME_IP to any port 1935 proto tcp
ufw enable
```

Check it:

```
ufw status verbose
```

If your home IP changes often, you can instead allow `1935` from anywhere and
rely on the stream key alone (managed on the admin dashboard), but pinning it to
your IP is stronger. If
your IP changes and you can no longer publish, update the rule:

```
ufw delete allow from OLD_HOME_IP to any port 1935 proto tcp
ufw allow from NEW_HOME_IP to any port 1935 proto tcp
```

## 1.5 Get the project onto the server

```
git clone YOUR_REPO_URL upperroom
cd upperroom
```

Continue with `docs/02-cloudflare.md`.
