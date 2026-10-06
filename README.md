<p align="center"><img src="docs/assets/icon0.jpg" width="112" alt="JIANZI icon"></p>

<h1 align="center">JIANZI · 减字</h1>
<p align="center"><img src="https://img.shields.io/badge/version-0.1.0-orange.svg" alt="Version 0.1.0"></p>
<p align="center"><strong>Compile your own jianzi font.</strong><br>A database-driven editor and compiler for guqin jianzi (减字) fonts.</p>

<p align="center">
  <a href="docs/zh/README-zh.md">简体中文</a>
</p>

**JIANZI is in active development. Current version: 0.1.0.**

## Introduction 

JIANZI is a database-driven pipeline for guqin jianzi (减字) notation fonts. Design your own jianzi "components", assemble your own jianzi library in the desktop GUI, then compile that library into a TTF/OTF font. OpenType GSUB ligatures turn input codes such as 「大九挑七」 directly into jianzi glyphs, so a compiled font works in any OpenType-aware editor.

The destination is a complete jianzi authoring experience: fonts that typeset notation as naturally as text, style packs that swap component outlines while keeping placement parameters, and a dedicated jianzi input method planned on top of the font pipeline. **These are roadmap commitments; the edit–compile font pipeline is the foundation already in place.**

## About Jianzipu（减字谱）


## Jianzi Font File

We will provide jianzi font files, including FFT and OFT.

## Installation

### Hands-on Installation

Clone the repo:

```bash
git clone https://github.com/ggbond-quantum/jianzi.git
cd jianzi
```

Create the environment and install dependencies with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Launch the desktop app:

```bash
uv run jianzi-gui
```

### Using the Installation Package

Coming soon...

## Tools

- [make_test_page.py](tools/make_test_page.py): generates a font test page and a specimen image for a compiled jianzi font, and runs release checks against it — full ligature shaping, simplified/traditional input equivalence, the 〇 prefix rule for single-character codes, and GDI installability on Windows.For detailed usage:

```bash
uv run python tools/make_test_page.py --help
```

