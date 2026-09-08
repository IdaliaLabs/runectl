# Runectl Mark

Product mark for `runectl`, distinct from the Idalia Labs company mark ("Clock at Nine",
see `../../brand/README.md` in the parent `IdaliaLabs` directory). Each Idalia product
gets its own mark under this same construction rule; this is runectl's.

## The mark: "Fork"

A single stem splitting into two — the bone of most Elder Futhark rune letterforms,
carved rather than drawn: straight strokes only, no curves, exact mirror symmetry.
No existing rune is traced whole, and nothing here is a stylized weapon, helmet, or
game asset — it's an original stroke arrangement built the way runes were actually
made.

Also reads as a literal fork/branch, which fits a tool that splits one CTF run across
categories.

`viewBox 0 0 64 64`, stroke `#000` or `#FFF`, `stroke-width 9`, `stroke-linecap butt`,
`stroke-linejoin round`. Same stroke weight as the company mark, so the two sit
together as siblings rather than competing systems.

## svg/
Vector masters, transparent background, resize freely with no quality loss.
- `mark-black.svg` — black mark, for light backgrounds
- `mark-white.svg` — white mark, for dark backgrounds

## png/
Raster forms of the mark alone, 1024×1024, matching the parent brand's `logo/png/` layout.
- `mark-black.png` / `mark-white.png` — transparent background
- `profile-on-white.png` / `profile-on-black.png` — solid background, safe for a circular avatar crop

Cut with macOS QuickLook (`qlmanage -t`) rendering each colorway against both a white and
a black backing, then recovering true alpha per-pixel from the two composites — QuickLook
itself doesn't export transparent PNG directly. Platform banner/lockup sizes (like the
parent brand's `banners/`) aren't cut yet; regenerate everything from `svg/mark-*.svg`,
the source of truth, if these ever need to change.

## Why this mark differs from Clock at Nine

Clock at Nine is Idalia Labs, the company. Fork is runectl, the product. Idalia plans
more than one product, so the company mark stays reserved for company-level surfaces
(the website, the GitHub org, LinkedIn) while each product carries its own mark built
on the same rule — straight-line construction, strict black/white, `stroke-width 9`.
