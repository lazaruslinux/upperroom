# Icons

`icon.svg` is the source of the app mark: a lowercase `u` on the app's near-black
surface, in the default accent green. It is drawn as a stroked path rather than
as text, so it renders identically without a font to load and stays legible at
16 pixels.

`icon-maskable.svg` is the same mark scaled to sit inside the safe circle Android
crops maskable icons to.

Everything else here is rasterized from those two, once, and committed:

| file | source | size |
|---|---|---|
| `icon-192.png` | `icon.svg` | 192 |
| `icon-512.png` | `icon.svg` | 512 |
| `icon-512-maskable.png` | `icon-maskable.svg` | 512 |
| `apple-touch-icon.png` | `icon.svg` | 180 |
| `../../favicon.ico` | `icon.svg` | 16 and 32 |

```
rsvg-convert -w 512 -h 512 icon.svg -o icon-512.png
rsvg-convert -w 192 -h 192 icon.svg -o icon-192.png
rsvg-convert -w 512 -h 512 icon-maskable.svg -o icon-512-maskable.png
rsvg-convert -w 180 -h 180 icon.svg -o apple-touch-icon.png
rsvg-convert -w 16 -h 16 icon.svg -o f16.png
rsvg-convert -w 32 -h 32 icon.svg -o f32.png
magick f16.png f32.png ../../favicon.ico
```

There is no generator script in the repo on purpose: it would be dead code after
one run, and rasterizing SVG from Python would mean a dependency this project
does not otherwise need.

`og-default.png` (1200x630) is the image link previews use. It is the wordmark on
the same background, set in Inter, the font already vendored in `../fonts`.

## Replacing the artwork

To use your own mark, replace the PNGs and `favicon.ico` with files of the same
names and sizes. Nothing in the code refers to the artwork itself, only to those
filenames, so no code change is needed. Keep the maskable variant's mark inside
the middle 80% of the canvas or Android will crop it.

The icons carry the default green accent. An operator running a different accent
still gets these, which is the deliberate trade against shipping four sets.
