# Inter (bundled)

`InterVariable.woff2` — Inter v4.1 variable font (weights 100–900), from the
official release: https://github.com/rsms/inter/releases/tag/v4.1
(asset `Inter-4.1.zip`, sha256 9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e,
entry `web/InterVariable.woff2`, sha256 693b77d4…, 352,240 bytes).

Licensed under the SIL Open Font License 1.1 — see `Inter-LICENSE.txt`,
which ships alongside the font as the license requires.

Bundled (v6.9.2) so the desktop and the phone companion render the SAME
typeface. Before this, `font-family: Inter, 'Segoe UI', Roboto, …` fell
through to Segoe UI on Windows and Roboto on Android, because Inter was
installed on neither. The desktop copy lives in `desktop/renderer/fonts/`
(packaged into the asar); this copy is served by the backend at
`/static/fonts/InterVariable.woff2` for the companion page.
