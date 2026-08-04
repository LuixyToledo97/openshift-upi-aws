# Bundled fonts

Two variable fonts, bundled rather than fetched, so the UI looks the same on
every machine and works with no internet at all — which matters for a tool you
may well be running while your only cluster is on fire.

Each family is one variable file per subset, so every weight from 100 to 900
comes out of a single download. 176 KB for all four.

| File | Family | Subset | Licence |
|---|---|---|---|
| `inter-latin.woff2` | [Inter](https://rsms.me/inter/) | latin | SIL Open Font License 1.1 |
| `inter-latin-ext.woff2` | Inter | latin-ext | SIL Open Font License 1.1 |
| `jetbrains-mono-latin.woff2` | [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | latin | SIL Open Font License 1.1 |
| `jetbrains-mono-latin-ext.woff2` | JetBrains Mono | latin-ext | SIL Open Font License 1.1 |

Both licences permit bundling and redistribution, including commercially, as
long as the fonts are not sold on their own and the copyright notice travels
with them — which is what this file is for.

- Inter © 2016 The Inter Project Authors (https://github.com/rsms/inter)
- JetBrains Mono © 2020 The JetBrains Mono Project Authors
  (https://github.com/JetBrains/JetBrainsMono)

Full licence text: https://openfontlicense.org

Subsets taken from the Google Fonts CDN, which serves the same upstream files
split by `unicode-range`. Only `latin` and `latin-ext` are bundled; the UI is
in English and the config is ASCII, so the Cyrillic, Greek and Vietnamese
subsets would be dead weight.
