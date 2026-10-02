---
version: 1
slug: "web-watch-html"
primary_target: "web/watch.html"
related_targets: ["web/admin.html","web/index.html","web/home.html"]
---

# Surface brief: the whole site (viewer pages + operator dashboard)

## Scope and mode
- Viewer pages (sign-in and invite link, home when offline, watch, browse, media, clip, options): **Experience** on /watch (the visitor is inside the stream), Operate elsewhere.
- Operator dashboard (/admin, mod, analytics, setup): **Operate**.
- Replacement visual world. Product truth, copy, function and every feature stay; the old look is evidence only.

## Audience, job, constraints
- Friends on phones, arriving from a group-text link while the operator plays games. Job: tap, land in the room, watch, chat. The watch link lands anyone with an account straight in the room.
- Operator job: open the dashboard, go live from OBS, copy the watch link, watch the room. The dashboard opens on one Manage Stream screen; everything else sits behind a quiet menu.
- Anti-goals (the operator's words): gimmicky or gamer RGB; busy on a phone. No pixel or dot-matrix fonts. No religious imagery.
- Static pages, no build step, strict CSP (no inline script or style attributes), versioned asset URLs, self-hosted fonts.

## Direction contract
THESIS: The site is a broadcast booth after hours. It refuses the platform layout (top bar, rail, badge soup): one lit lamp means live, the stream is the program monitor, and the people in the room are lit lines on a call board.

OWN-WORLD: Acoustic charcoal grounds (#111214, panel #1b1d21, hairline #2a2d33), anodized mid-gray hardware, warm white ink (#efece6). ON AIR red (#ff3b2f) is reserved for the live lamp and nothing else; the channel accent marks primary actions and selection only. Flat panels, hairline seams, engraved-style condensed small-caps labels; no glow except the lamp.

STORY: A friend understands in one glance whether the room is on air, sees who is there, and talks. The operator sees each go-live step become true and sends the link.

FIRST VIEWPORT: Phone /watch: a slim lamp strip across the top edge (lit ON AIR, or dark glass reading OFF AIR), the stream full width beneath as the program monitor with nothing drawn over it, one call-board row of lit tiles for who is here, chat filling the rest, the composer at the thumb. Desktop /admin: the Manage Stream screen, the program monitor, chat and call board on top, three ticking steps under them, one loud "Copy watch link" action.

FORM: Studio booth, after hours; position 6 of 7 on my ordered grounded list; seed key 29541f9c (re-roll 1). Raises kept: nothing overlays the stream; empty and offline states designed as deliberately as live; one loud word per screen; steps tick as they become true; one strict spacing grid; one centered axis on phones.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Build path
Code-led (no image generation on this machine). A static prototype of phone /watch and desktop Go live is shown to the operator for approval before the build.

## Open decisions
- Display face: a self-hosted condensed grotesk for the lamp, display headings and the engraved labels; body stays a workhorse sans.
- Compact controls (owner, 2026-09-30: "The buttons and font dont need to be so big"): every button, the loud word included, is Inter in sentence case, 14px and 36-40px tall under a mouse, 15px and at least 44px tall on touch; the loud word keeps the accent fill and full width at a primary button's size. Display headings stay condensed but smaller: page titles about 24px phone / 28px desktop, the off-air headline 26px / 32px, step titles 18px.
- Accent presets must never include a red that competes with the lamp.
