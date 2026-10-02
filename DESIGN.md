---
name: upperroom
description: A private room for one live stream, drawn as a broadcast booth after hours. Dark only.
colors:
  accent: "#6ab48a"
  accent-amber: "#c2a05c"
  accent-blue: "#7aa3c0"
  accent-ghost: "#9aa39a"
  accent-ink: "#11140f"
  lamp: "#ff3b2f"
  lamp-face: "#ff5a47"
  lamp-edge: "#ff6d5e"
  lamp-ink: "#fff7f2"
  glass: "#191616"
  glass-edge: "#262020"
  glass-ink: "#948582"
  ground: "#111214"
  panel: "#1a1c20"
  raised: "#22252a"
  seam: "#2c2f35"
  hardware: "#3d4148"
  booth: "#0c0d0f"
  screen: "#050506"
  ink: "#efece6"
  ink-2: "#aeafb2"
  ink-3: "#8a8d93"
typography:
  display:
    fontFamily: "Barlow Condensed, Arial Narrow, Roboto Condensed, sans-serif-condensed, Liberation Sans Narrow, DejaVu Sans Condensed, sans-serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 0.95
    letterSpacing: "0.02em"
  headline:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "24px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.04em"
  title:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.02em"
  body:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
  button:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.1
  label:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.14em"
  lamp:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.22em"
rounded:
  corner: "2px"
  switch: "14px"
spacing:
  u: "4px"
  u2: "8px"
  u3: "12px"
  u4: "16px"
  u5: "20px"
  u6: "24px"
  u10: "40px"
components:
  button:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink-2}"
    typography: "{typography.button}"
    rounded: "{rounded.corner}"
    padding: "0 16px"
    height: "40px"
  button-hover:
    textColor: "{colors.ink}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.corner}"
    padding: "0 16px"
    height: "40px"
  button-loud:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.corner}"
    padding: "0 16px"
    height: "40px"
    width: "100%"
  button-loud-quiet:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
  field:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.corner}"
    padding: "0 12px"
    height: "40px"
  chip:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.corner}"
    padding: "0 12px"
    height: "36px"
  lamp-off:
    backgroundColor: "{colors.glass}"
    textColor: "{colors.glass-ink}"
    typography: "{typography.lamp}"
    rounded: "{rounded.corner}"
    padding: "7px 12px 6px 14px"
  lamp-on:
    backgroundColor: "{colors.lamp}"
    textColor: "{colors.lamp-ink}"
    typography: "{typography.lamp}"
    rounded: "{rounded.corner}"
    padding: "7px 12px 6px 14px"
  strip:
    backgroundColor: "{colors.booth}"
    textColor: "{colors.ink}"
    height: "48px"
  call-board-line:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0 12px"
    height: "40px"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.corner}"
    padding: "20px"
  menu-row:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.corner}"
    padding: "0 12px"
    height: "44px"
---

# Design System: upperroom

## Overview

**Creative North Star: "Studio booth, after hours"**

The site is a broadcast booth after the lights are down. One lamp says whether the room is on air, the stream is the program monitor, and the people in the room are lit lines on a call board. It refuses the platform layout (top bar, rail, badge soup): the stream and the people lead, and controls, settings and stats sit behind a quiet menu until someone reaches for them.

The material is acoustic charcoal and anodized hardware. Panels are flat and set into one dark ground, divided by 1px seams, with barely-there 2px corners. Labels read like engraving on a console: condensed, small caps, widely tracked, dim. Ink is a warm white, never pure. The one light in the room is the ON AIR lamp; everything else is lit only by contrast. Motion is sparse: a chat line rising 3px as it arrives, quick color changes, a step's tick fading in, and nothing at all under reduced motion.

The system is dark only. There is no light theme and none is planned; `color-scheme: dark` is declared at the root. Each install sets its own site name and one channel accent; the booth itself does not change.

**Key Characteristics:**
- One charcoal ground, flat panels, 1px seams, 2px corners.
- One red, one glow: the ON AIR lamp, echoed once by the live dot on Manage Stream.
- One channel accent, operator-chosen, for the primary action and the current selection.
- Barlow Condensed for the lamp, titles and engraved labels; Inter for everything read at length and for every button.
- One spacing unit (4px); every measure is a multiple of it.
- Nothing is ever drawn over a playing stream.

## Colors

A near-black charcoal booth in warm-white ink, with one reserved red lamp and one muted, operator-chosen accent.

### Primary
- **Booth Green** (`accent`): the default channel accent. It fills the one primary action per screen (the loud word, a submit, Send) and marks the current selection (an underline, a focus ring, the host's line on the call board, the next step's mark). It is never decorative. `accent-wash` (the accent at 12% alpha) exists for quiet fills.
- **Accent presets** (`accent-amber`, `accent-blue`, `accent-ghost`): the operator swaps the accent on the dashboard; each preset brings its own `accent-ink` for text on the fill. Every preset sits well away from the lamp's red, and a new preset must too.
- **Accent Ink** (`accent-ink`): text and icons on an accent fill; a near-black tinted toward the accent.

### Secondary
- **ON AIR Red** (`lamp`, with `lamp-face` and `lamp-edge` for the lit gradient and rim, and `lamp-ink` for its letters): the live lamp in the strip, and its one echo: the steady dot beside the dashboard's Manage Stream section link while on air. Nothing else anywhere on the site.
- **Dark Glass** (`glass`, `glass-edge`, `glass-ink`): the same lamp switched off, reading OFF AIR. A faintly warm black, so an unlit lamp still reads as a lamp.

### Neutral
- **Room Charcoal** (`ground`): the page ground everywhere.
- **Set Panel** (`panel`): a panel set into the ground; fields, modals, the menu sheet, the slate.
- **Raised Key** (`raised`): a control sitting on a panel; buttons, chips, call-board lines, hovered menu rows.
- **Seam** (`seam`): the 1px line between two surfaces; every border at rest.
- **Anodized Hardware** (`hardware`): hover edges, link underlines, scrollbar thumbs, rails.
- **Booth Black** (`booth`): the lamp strip and the composer, darker than the room so the edges of the screen recede.
- **Dead Screen** (`screen`): a monitor with nothing on it; behind video and thumbnails.
- **Warm White Ink** (`ink`), **Ink 2** (`ink-2`), **Ink 3** (`ink-3`): text in three strengths. All three clear 4.5:1 on the panel. Ink 2 is secondary text and resting control labels; Ink 3 is engraved labels, metadata and hints.

### Named Rules
**The One Red Rule.** The lamp's red is the lamp and its one echo, the steady live dot on the dashboard's Manage Stream section link, lit only while the lamp is. Errors, deletions and warnings never use a color: an error is plain ink with a small outlined warning mark beside it, and a destructive button is an ordinary key whose confirmation question is the warning.

**The One Accent Rule.** The channel accent marks the one primary action on a screen and the current selection. It never tints a surface, a heading or an illustration.

## Typography

**Display Font:** Barlow Condensed 500/600/700 (with Arial Narrow, Roboto Condensed and condensed system fallbacks), self-hosted, `font-display: block`
**Body Font:** Inter 400/500/700 (with system-ui), self-hosted, `font-display: swap`
**Label/Mono Font:** JetBrains Mono, only for stream keys, links and codes in fields

**Character:** A narrow, engraved console face against a workhorse sans. The condensed face names things and states the room's condition; Inter carries everything a person reads or presses. The display face blocks rather than swaps because a wide fallback at a phone's width is the wrong face, not a softer one.

### Hierarchy
- **Display** (700, 30px phone / 36px from 720px, line-height 0.95, uppercase): the door titles on sign-in, setup and shared clip. The product name in the setup title stays lowercase.
- **Headline** (700, 24px phone / 28px from 720px, line-height 1, 0.04em, uppercase): page titles. The monitor's word when there is no picture (OFF AIR, full, over) uses the same voice at 26px / 32px with 0.06em.
- **Title** (600, 16-18px, line-height 1.1-1.2, 0.02em, sentence case): step titles (18px), modal headings (18px), the slate title, media titles and call-board names (16px). Media page titles step up to 22px.
- **Body** (Inter 400, 15px, line-height 1.45, tabular numerals): chat, notes, settings. Secondary text 13-14px. Every text input is 16px so iOS never zooms on focus. Group notes cap at 68ch, monitor notes at 44ch.
- **Button** (Inter 500, 14px under a mouse / 15px on touch or under 560px; primary 600): every button, in sentence case.
- **Label** (600, 12px, 0.14em, uppercase, Ink 3): engraved field labels and small section headings. Dashboard group heads step up to 14px in Ink 2; role tags and "No picture" slates use 11px at 0.12-0.14em.
- **Lamp** (700, 14px, 0.22em, uppercase): ON AIR / OFF AIR only.

### Named Rules
**The Engraved Voice Rule.** Barlow Condensed is for the lamp, titles and the engraved labels that name a field or a section. It never sets a button, a paragraph or a chat line.

**The Sentence-Case Button Rule.** Buttons are Inter in sentence case at 14-15px, including the loud word. Uppercase belongs to the lamp, headlines and engraved labels.

## Layout

One spacing unit, 4px (`u`); every padding, gap and margin is a multiple of it, most often 8, 12, 16, 20, 24 and 40px. Section groups sit 40px apart; a group head carries a seam beneath it.

Viewer pages are one centered column (720px, wide pages 1120px) under the 48px lamp strip; doors (sign-in, setup, shared clip) are a 480px column. On a phone everything shares one centered axis. The watch room stacks strip, monitor, slate, call board, chat and the composer at the thumb; from 960px chat becomes a 380px column beside the monitor, the monitor is sized so the slate and board still fit beneath it, and the lamp takes the center of the strip.

The operator dashboard is up to 1440px wide. Its Manage Stream screen leads with the room panel (program monitor, slate, call board, readout) at full width and puts the three steps under it, 32px apart: stacked on a phone, side by side from 960px with the first step wider and seams between them. Section pages are a 760px column (1040px wide). Section links run along the strip from 1080px and lead the menu sheet below that.

Controls are compact under a fine pointer (40px buttons, fields and icon keys; 36px chips) and grow to at least 44px under a coarse pointer or below 560px. Breakpoints in use: 560, 640, 720, 960, 1080, 1460px.

## Elevation & Depth

The system is flat. Depth comes from tonal layering (booth, ground, panel, raised, darkest to lightest by role) and 1px seams, never from drop shadows. The lamp is the exception, and the only light source.

### Shadow Vocabulary
- **Lamp glow** (`box-shadow: 0 0 18px rgba(255, 59, 47, 0.45), 0 0 2px rgba(255, 59, 47, 0.9), inset 0 1px 0 rgba(255, 255, 255, 0.35)`, with `text-shadow: 0 0 6px rgba(255, 220, 210, 0.6)`): the lit lamp, and the live dot without the inset highlight.
- **Dark glass** (`box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03), inset 0 -8px 12px rgba(0, 0, 0, 0.35)`): the unlit lamp's recessed face.
- **Selection line** (`box-shadow: inset 0 -2px 0` accent): the underline on a selected tab, pressed chip, current strip section or chosen swatch. It is a drawn line, not elevation.

### Named Rules
**The One Glow Rule.** Nothing glows but the ON AIR lamp and its live dot. No accent glow, no hover glow, no colored shadows.

**The Flat Panel Rule.** Panels, modals and the menu sheet are flat: a seam and a lighter fill set them apart. A modal sits on the room dimmed to 72% black, not on a shadow.

## Shapes

Barely softened rectangles: every panel, button, field, chip, tile and thumbnail uses a 2px corner (`corner`). Borders are 1px seams that shift to hardware on hover and to the accent on focus or selection. The only rounder form is the switch (a 14px-radius track with a round knob); avatars are square with the 2px corner. Monitors and thumbnails are strict 16:9. Icons are authored inline SVG on a 24px grid at one stroke weight (1.75, round caps and joins), drawn at 20px.

## Components

### Buttons
Raised keys on a console: compact, quiet at rest, never shouting except once per screen.
- **Shape:** 2px corner, 1px seam border, 40px tall (44px on touch), 0 16px padding, 8px icon gap.
- **Ordinary key:** raised fill, Ink 2 label; hover brings the label to ink and the edge to hardware.
- **Primary:** accent fill and border, accent ink, weight 600; hover brightens the fill by 6%.
- **Loud word:** the one thing the screen is for (Copy watch link, Sign in, Watch). Accent fill at full width, at a primary button's size. Off air it waits as a quiet key: raised fill, ink label, a hardware inset edge.
- **Danger:** looks exactly like an ordinary key; the confirmation it asks is the warning.
- **Link button:** underlined Ink 2 text with a hardware underline, for quiet asides.
- **Icon key:** 40px square (44px on touch), transparent until hovered.
- **Disabled:** 45% opacity, not-allowed cursor.

### Chips
- **Style:** 36px (44px on touch) raised keys with a seam border and 14px Inter label, for the slate's Sound and full-screen keys and similar tools.
- **State:** pressed or on draws the accent selection line and a seam in the accent.

### Cards / Containers
- **Corner Style:** 2px.
- **Background:** panel on the ground; raised for an inline form opened inside a panel.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px seam.
- **Internal Padding:** 12px for slates and boards, 20px for modals, 12-16px for readouts and inline forms.

### Inputs / Fields
- **Style:** panel fill, 1px seam, 2px corner, 40px tall (44px on touch), 0 12px padding, Inter 16px in ink, Ink 3 placeholders. An engraved label sits 8px above its field.
- **Focus:** keyboard focus turns the border to the accent; pointer focus to hardware. Everything else takes a 2px accent outline at 2px offset.
- **Error:** plain ink text with the outlined warning mark; never a red border.
- **Switch:** a 48 by 28px track that fills with the accent when on, its knob sliding 20px; its reach extends past its edges on touch.

### Navigation
- **The lamp strip:** 48px of booth black across the top of every page, a seam beneath. Menu key and site name (16px condensed, 0.04em) on the left; the lamp and the on-air clock on the right, centered from 960px.
- **Menu sheet:** a flat 300px panel under the menu key, closed by Escape, an outside click or the key. Rows are 44px, Inter 15px in Ink 2 with an Ink 3 icon; hover and the current page take the raised fill, and the current page's icon takes the accent.
- **Dashboard sections:** condensed 14px uppercase words along the strip; the current one takes the raised fill and the accent selection line. While on air, Manage Stream carries an 8px lamp-red dot with the lamp's glow, steady, never pulsing, in the strip and in the menu.

### The ON AIR Lamp (signature)
A small engraved lamp in the strip. Off, it is dark glass reading OFF AIR in a dim warm gray. Live, it lights: a red gradient face, a pale rim, near-white letters and the one glow on the site. It changes over 300ms.

### The Program Monitor (signature)
The stream, full width at 16:9 on a dead-black screen. Nothing is ever drawn over a playing picture. When there is no picture, a full-screen card replaces it (never overlays it) with one condensed word (OFF AIR, the room is full, it is over), a short note and at most a couple of keys. Beneath it, the slate names what is on and holds the sound.

### The Call Board (signature)
Who is in the room, one lit tile each: a 40px raised key (44px on touch) with the person's initial on a phone or name on a desktop, in condensed 16px, and a 2px lit line along its bottom edge in ink. The host's line is the accent. Someone away dims to Ink 3 and their line almost goes out.

### Go Live Steps (signature, dashboard)
Three steps that tick as they become true. Each has a 32px mark: dark with its number while waiting, accent-edged when it is the step to do now, filled with ink and a tick once done. Step titles are condensed 18px; the state line under each is Inter 15px.

### Chat
Messages stack 12px apart with a 28px square avatar, a bold 14px name, a 12px time and 15px body; a new line rises 3px over 180ms. A highlighted message lifts onto the raised fill with an accent rule above it. The composer sits at the thumb on booth black, a field and the accent Send key.

## Do's and Don'ts

### Do:
- **Do** keep the ON AIR lamp (and its one echo, the live dot) the only red and the only glow on any surface.
- **Do** give each screen exactly one loud word in the accent at full width; everything else is an ordinary key.
- **Do** build depth from ground, panel and raised fills with 1px seams and 2px corners.
- **Do** set every padding, gap and margin as a multiple of 4px.
- **Do** keep buttons Inter in sentence case, 14px and 40px tall under a mouse, 15px and at least 44px on touch.
- **Do** name small sections and fields with engraved labels: Barlow Condensed 12px, 0.14em, uppercase, Ink 3.
- **Do** design the empty, offline, full and slow-storage states as deliberately as the live one, and replace the monitor's picture rather than overlaying it.
- **Do** keep text inputs at 16px and honor reduced motion by stopping every animation and transition.

### Don't:
- **Don't** add a light theme or light surfaces; the booth is dark only.
- **Don't** use red, or any hue near the lamp, for errors, deletions, badges or an accent preset.
- **Don't** draw anything over a playing stream: no chat overlay, badges, controls or captions on the picture.
- **Don't** use drop shadows, accent glows or colored shadows for elevation.
- **Don't** set buttons, paragraphs or chat in Barlow Condensed, or buttons in uppercase.
- **Don't** use the accent to decorate; it marks the primary action and the current selection only.
- **Don't** use pixel or dot-matrix fonts, gamer RGB, or religious imagery.
- **Don't** fetch fonts or assets from a third party; every face is self-hosted.
