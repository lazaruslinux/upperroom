# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **The operator**: one person who streams from OBS to a small circle of people they know, usually while playing games. They also run the server. Their job in the product is short and frequent: go live, send the link, watch the room, moderate now and then, check the dashboard afterwards.
- **The circle**: friends and family the operator invited. Most watch and chat on their phones, sometimes on a laptop or a TV. Each holds an account, made from an invite link or code.
- **Other operators** (second audience): self-hosters who clone the public repository to run their own private channel. Anything that could differ per install is a dashboard setting, not a constant, but their needs never water down the first two audiences.

## Product Purpose

upperroom is a self-hosted, single-channel live streaming site with accounts and chat: a private room for a stream, owned by whoever runs it. Success is an operator who opens OBS, presses start, pastes one link into a group text, and has their people watching and talking within a minute, on a phone, with nothing in the way.

## Positioning

A private upper room, not a platform: nobody watches without an account the operator issued, there is no discovery, no public directory, no ads, and nothing phones home. The name carries that meaning on purpose, a room upstairs where invited people gather. The meaning is carried by how the product behaves, not by imagery: no religious imagery.

## Operating Context

- The operator broadcasts from OBS over RTMP to their own server; MediaMTX repackages it as low-latency HLS with no transcoding, so every viewer pulls the full encoder bitrate.
- Friends reach the site from a link in a text message, most often on a phone, often while doing something else. Sessions are evening-length; chat is per night, not per broadcast.
- The operator watches their own room from the dashboard, which frames the watch page, and from their phone.
- Saved broadcasts and clips live on a separate storage service, possibly on another machine; they can be slow or briefly unavailable, and the site must say so plainly rather than break.

## Capabilities and Constraints

- Accounts are required to watch, permanently; there is no way in without one. Newcomers get a single-use invite link. A shared clip (admin-published, per clip) is the only thing a stranger can watch without an account.
- Features: live watch page with chat and presence, viewer limit, invite codes and links, moderation (commands, bans, timeouts, word filter, slow mode), saved broadcasts (VODs) with chat replay, viewer clips, likes and comments, channel points and highlights, an OBS chat overlay, go-live push notifications, analytics for the operator.
- Parked and hidden (code kept): theater mode (playing titles from a media library through a projector service). The notification bell and the inbox were removed.
- Static front end with no build step (plain HTML, CSS and JavaScript served by Caddy, versioned asset URLs); a FastAPI service behind it. Pages must work behind a strict Content Security Policy.
- Small servers: one CPU, little memory, modest disks. Nothing heavy runs in the browser or on the server.
- Interface copy is terse. It is a user interface, not an instruction manual; the long explanations live in the docs.
- The single-channel scope is deliberate. There is one stream, one room.

## Brand Commitments

- Product name: **upperroom**, always lowercase. Each install shows its own configured site name and accent color; the product name stays out of the viewer's way.
- The `u` monogram is an interim mark; the operator's own art will replace it as a PNG with no code change.
- The app credits its developer with a single "developed by" link to the developer's own site. App interfaces never link the source code host directly; the operator dashboard's source and docs links are the deliberate exception that keeps the license's network-use offer honest.
- No pixel or dot-matrix fonts anywhere a viewer reads.
- Binding visual brief from the operator: a darkened, sleek, modern design. No religious imagery.

## Evidence on Hand

- A demo profile ships a fictional brand ("Northwind Live") with synthetic video, demo accounts and seeded chat, for screenshots and QA (`docs/07-demo.md`, `gate/demo_seed.py`).
- README screenshots in `docs/screenshots/` are of the demo brand only.
- There are no testimonials, user counts, press or case studies. None may be invented.

## Product Principles

1. **One minute from OBS to friends watching.** Every surface is judged by how much it shortens go live, share, watch.
2. **Private by construction.** Every way in is an account or a link the operator made; nothing is public by accident, and the interface never suggests otherwise.
3. **The room, not the platform.** The stream and the people in it lead; controls, stats and settings stay out of the way until someone reaches for them.
4. **Phones first for viewers, one screen for the operator.** A friend on a phone gets the whole experience; the operator can run a night from a single dashboard view.
5. **Say what is happening.** Offline, full, slow storage: the interface names the state plainly and briefly instead of failing silently.

## Accessibility & Inclusion

- Viewers are a mixed-age circle of friends and family on phones: readable type sizes, strong contrast, generous touch targets, and nothing that depends on hover.
- Respect reduced-motion preferences; no flashing effects near live video.
