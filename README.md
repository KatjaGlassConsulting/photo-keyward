# photo-keyward

Turn thousands of photos into a collection of the ones worth keeping.

Photo-keyward is a local, Explorer-style image browser built for the job of reducing large photo collections to the good - and ultimately the best - photos. Work folder by folder in a thumbnail grid. Quickly review thousands of images, zoom in on anything promising, and tag photos with keywords as you go. Instead of trying to make the final selection in one pass, keywords let you mark and revisit different groups of photos and gradually narrow the collection down.

The Keys tab is where this becomes powerful: filter the grid by any combination of keywords to bring back exactly the photos you are looking for. Because once a folder contains several thousand images, finding the best photos is no longer just a browsing problem. It becomes a filtering and selection problem.

The ultimate goal is simple: Start with thousands of photos. Keep only the ones worth keeping. Keywords are written directly into the image files as standard IPTC and XMP metadata, so they stay with the photos and can be used by other applications that support these standards. Keywords can be organised into hierarchies using |, with subgroups available for structured tagging and filtering.

There is no catalogue to import or maintain. No cloud upload. No external service.

Your photos stay where they are, and everything runs locally on your machine.

## Contents

- [Vibe coded](#vibe-coded)
- [Setup](#setup)
  - [Requirements](#requirements)
  - [Platform](#platform)
  - [Installing exiftool](#installing-exiftool)
  - [Run](#run)
  - [Configuration](#configuration)
- [Features](#features)
  - [Layout](#layout)
  - [Tagging with keywords](#tagging-with-keywords)
  - [The keyword vocabulary](#the-keyword-vocabulary)
  - [Managing keywords](#managing-keywords)
  - [Filtering by keyword](#filtering-by-keyword)
  - [Shortcuts](#shortcuts)
  - [Trash](#trash)
  - [Scripts](#scripts)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Vibe coded

This project is vibe coded, and the acknowledgment matters more than the
label. The application code, the console scripts, the specifications and plans
under `docs/superpowers/`, and this README were all written by
[Claude](https://www.anthropic.com/claude) through
[Claude Code](https://claude.com/claude-code), working from prompts rather than
from a human writing the lines. Verification throughout has been manual:
running the app against real photo folders and checking the result on screen.

What that means if you are thinking of using it:

- **There are no automated tests and no CI.** Every plan document under
  `docs/` records the same decision. Nothing here is covered by a test suite.
- **It modifies your files in place.** The **Scripts** menu and the keyword
  panel write EXIF into your images through exiftool, and `Del` moves files
  into a `trash` folder. Work on a copy of anything you care about.
- **Read the code before you rely on it.** No human has line-by-line reviewed
  all of it.

The upside of the same process is that the reasoning is unusually well
documented. The nine plans and ten specifications under `docs/superpowers/`
record what each feature was meant to do and why, and are a better guide to the
intended behaviour than the code alone.

## Setup

What you need installed, where it runs, and how to start it.

### Requirements

Three things have to be installed before `uv sync` will get you a running app.

| Requirement | Version | Why it is needed |
| --- | --- | --- |
| [Python](https://www.python.org/downloads/) | 3.11 or newer | The floor declared in `pyproject.toml`. Developed and run on 3.13. |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | any recent release | Creates the virtual environment and installs the exact versions pinned in `uv.lock`. |
| [exiftool](https://exiftool.org) | developed against 13.58 | A separate program, not a Python package. It does all EXIF reading and writing. |

Exiftool is a hard requirement, not an optional extra. The app reads EXIF
automatically when you open a folder, the keyword panel writes tags back
through it, and every entry in the **Scripts** menu shells out to it. Without
it the browser still shows thumbnails, but keywords, dates and the whole
Scripts menu do nothing. See [Configuration](#configuration) if it is installed
somewhere your shell cannot find.

Three Python packages come in automatically with `uv sync`, so you do not
install them yourself:

- **PySide6** - the Qt 6 bindings that provide the entire user interface.
- **Pillow** - decodes and scales images for the thumbnail grid and preview.
- **python-dotenv** - reads the optional `.env` described below.

### Platform

Developed and used on Windows 11, which is the only platform it has been run
on. Nothing in the code is deliberately Windows-only, so macOS and Linux may
well work, but neither is tested and the shell snippets below are PowerShell.
The fallback locations searched for exiftool are Windows paths, so on macOS or
Linux keep exiftool on your `PATH` or set `EXIFTOOL_PATH`.

### Installing exiftool

Exiftool is a standalone program rather than a Python package, so a package
manager is the easiest route.

```powershell
winget install -e --id OliverBetz.ExifTool   # Windows
```

```bash
brew install exiftool                     # macOS
sudo apt install libimage-exiftool-perl   # Debian, Ubuntu
sudo dnf install perl-Image-ExifTool      # Fedora
sudo pacman -S perl-image-exiftool        # Arch
```

Each of those puts `exiftool` on your `PATH`, which is all the app needs.
There is nothing further to configure.

If you prefer the portable Windows build from [exiftool.org](https://exiftool.org),
unzip it wherever you like and **rename `exiftool(-k).exe` to `exiftool.exe`**.
The `-k` in that filename tells the program to wait for a keypress before it
exits, which leaves the app blocked on a read that never returns. Then either
add the folder to your `PATH` or point `EXIFTOOL_PATH` at the executable, as
described under [Configuration](#configuration).

Either way, confirm it before starting the app:

```powershell
exiftool -ver
```

### Run

```powershell
uv sync
uv run keyward
```

### Configuration

The app looks for `exiftool` on your `PATH` first, so if you installed it with
a package manager there is nothing to configure.

If exiftool lives somewhere the shell cannot find it - a portable unzipped
copy, for instance - point at it with an `.env` file in the project root:

```powershell
copy .env.example .env
```

Then set the one key it contains to the full path of the executable:

```ini
EXIFTOOL_PATH=C:\tools\exiftool-13.58_64\exiftool.exe
```

`.env` is git-ignored, so your machine's paths stay out of the repository.
`.env.example` is the committed template and carries the same notes.

A few details worth knowing:

- Resolution order is `EXIFTOOL_PATH`, then `exiftool` on `PATH`, then the
  usual Windows install folders under *Program Files* and *LocalAppData*.
- A real environment variable beats the `.env` entry, which lets you override
  the checked-out value for a single shell or a CI run.
- A path that does not exist is ignored rather than fatal, so a stale `.env`
  still leaves a working `PATH` installation usable.
- Do not quote the value. `~` and environment variables in it are expanded.
- The file is read once at startup - restart the app after editing it.

## Features

What the app does once it is running.

### Layout

- **Left:** two tabs - **Explorer**, the folder tree over the whole filesystem,
  and **Keys**, which filters the grid by keyword.
- **Right (top):** image grid with continuous zoom (64–1024 px). Thumbnails are
  unlabeled; hover for the filename and path.
- **Bottom:** details strip showing the selected image, its filename, path,
  dimensions, size and EXIF data. Hide it with `Ctrl` `D` when you want the
  grid to have the whole window; the setting is remembered.
- **Status bar:** how many images the folder holds - `120 images`, or
  `42 of 120 images` while the **Keys** filter is hiding some, or
  `3 of 120 selected` once you pick images out.

### Tagging with keywords

The panel on the right is where tagging happens. Select one or more images in
the grid, then tick a keyword to write it into every selected image, or untick
it to remove it. Writes go straight into the files through exiftool, in the
background - there is no save step and nothing to confirm.

The checkbox reflects the whole selection, so with several images picked out:

| Checkbox | Meaning |
| --- | --- |
| Checked | Every selected image carries the keyword. |
| Unchecked | None of them do. |
| Partially checked | Some do, some do not. |

Clicking a partially checked row applies the keyword to the images still
missing it, which is usually what you want after picking out a mixed group. The
partial state is only ever a description of the selection - you cannot set it
yourself.

Type into the box at the top to create a keyword and add it to the current
folder in one step. A name that already exists flashes the box red rather than
creating a duplicate.

Use `|` to build a hierarchy. `Travel|Egypt|2025` is one keyword, displayed as
*Reise ▸ Egypt ▸ 2025* in italics to set it apart from flat ones. Hierarchical
keywords are written to `XMP-lr:HierarchicalSubject`; flat ones to
`IPTC:Keywords` and `XMP:Subject`. They are separate tags, so `Egypt` and
`Reise|Egypt|2025` are two different keywords that can both be applied to the
same image.

Keywords are listed alphabetically, with accents folded so `Ägypten` sorts under
A rather than after Z, case ignored so `Beach` and `beach` sit together, and
hierarchies compared level by level so a parent stays next to its children.

If a write fails, it is not silent: a warning triangle with a count appears at
the right-hand end of the status bar, and clicking it lists what went wrong.

### The keyword vocabulary

Keywords live in a global vocabulary that persists across restarts, kept in
`%LOCALAPPDATA%\photo_keyward\keywords.json` (`~/.config/photo_keyward/` where
`LOCALAPPDATA` is not set). Anything you create in one folder is available in
every other folder and after the next start.

Each folder also has its own **visible subset** - which keywords the right-hand
panel shows there. A folder you have never opened inherits the entire
vocabulary, so the panel is never empty just because the folder is new. From
there you can prune it per folder through **Manage…**, which is worth doing once
a vocabulary grows past what is relevant to any one shoot. Pruning is a display
choice only: it never removes keywords from images, and the **Keys** tab still
shows any keyword actually found in the folder's photos.

Seeding is a snapshot rather than a subscription. A keyword created later
appears in the folder you created it in, not in every folder already seeded -
add it elsewhere through **Manage…**, or prune a folder to nothing and reopen it
to inherit the current vocabulary afresh.

The subsets live beside the vocabulary in `folder_subsets.json`. Both files are
written whole on every change, through a temporary file, so an interrupted write
cannot leave a half-written one behind. If either file ever *is* unreadable, it
is renamed to `<name>.corrupt-<timestamp>` and the app starts with an empty list
rather than overwriting it - the warning names the file so you can rename it
back after fixing it.

### Managing keywords

**Manage…** at the bottom of the keyword panel opens a dialog listing the whole
vocabulary, with a checkbox per keyword controlling whether it appears in the
current folder's panel. Those ticks apply on **OK**; everything else in the
dialog - creating, deleting, importing - takes effect immediately, so **Cancel**
undoes the ticks but not those.

**✕** deletes a keyword from the vocabulary entirely, after a confirmation.
Images that already carry it keep it - deleting only stops it appearing in any
folder's panel, and in the **Keys** tab it will still show up for folders whose
images use it.

**Import from folder…** reads every image directly in the current folder and
adds the keywords it finds to the vocabulary. Images the app has not read yet
are read first, with a progress dialog you can cancel; then it reports what it
found - *Found 12 keywords, 9 not yet in the vocabulary* - and adds them on
confirmation, ticked for the current folder. This is the quickest way to adopt a
collection that was already tagged in another program. It does not recurse into
subfolders.

### Filtering by keyword

The **Keys** tab lists every keyword available in the current folder: the
keywords configured for it, plus any others found in its images, in one
alphabetical list. All start checked, so the grid shows everything.

Uncheck keywords to narrow the grid. Several checked keywords mean *or* - an
image is shown if it carries at least one of them. **(no keyword)** at the top
matches images with no keywords at all, and **Select all** toggles between all
and none.

The filter resets to everything whenever you change folder, and applies to the
current folder only.

A keyword that appears while you are filtering normally arrives checked, so
nothing disappears from the grid on its own. The one exception is the untagged
backlog: when **(no keyword)** is the *only* checked row and you create a new
keyword in the right-hand panel, that keyword arrives unchecked - so each image
you tag leaves the grid and the pile visibly shrinks.

To have keywords to filter on, the app reads EXIF automatically when you open
a folder and at startup. `Ctrl` `E` is only needed to re-read a folder after
changing files outside the app. Until a read finishes, images are treated
as unknown and stay visible rather than being hidden.

### Shortcuts

| Action | Shortcut |
| --- | --- |
| Zoom in | `Ctrl` `+` (or `Ctrl` `=`) |
| Zoom out | `Ctrl` `-` |
| Reset zoom | `Ctrl` `0` |
| Zoom with the mouse | `Ctrl` + wheel |
| Re-read EXIF for the folder | `Ctrl` `E` |
| Clear the selection | `Esc` |
| Show/hide the details strip | `Ctrl` `D` |
| Move selection to trash | `Del` |
| Undo one delete (3 deep) | `Ctrl` `Z` |

### Trash

`Del` moves the selected images into a `trash` subfolder of the folder they
live in, creating it if needed. Files are moved, not deleted - restore them
with any file manager.

Each `Ctrl+Z` undoes one delete - the most recent one not yet undone - putting
those files back where they came from. Press it again to step back a further
delete, up to three presses. The history is per-session: it is not saved when
the application closes, and anything further back is recovered from the `trash`
folder instead.

A matching `.orf` or `.xmp` sidecar moves with its image, in both directions.
If a file of the same name is already in the way, the whole group is left in
place and a warning is recorded rather than anything being overwritten.

### Scripts

Available from the **Scripts** menu and as console commands. All operate on one
folder, non-recursively, and need exiftool available - see
[Configuration](#configuration).

| Script | Command | Effect |
| --- | --- | --- |
| Export Dates from ORF | `keyward-export-dates` | Writes each ORF's `DateTimeOriginal` to `dates.json`. |
| Import Dates to JPG | `keyward-import-dates` | Applies `dates.json` to same-stem JPGs. |
| Export Metadata | `keyward-export-metadata` | Writes JPG dates, keywords and hierarchy to `metadata.json`. |
| Import Metadata | `keyward-import-metadata` | Applies `metadata.json` to JPGs - dates overwrite, keywords are additive. |
| Set Date from Filename | `keyward-set-date-from-filename` | Sets `DateTimeOriginal` + `FileCreateDate` from a `YYYY-MM-DD` filename prefix. |
| Set Date from Metadata | `keyward-set-date-from-metadata` | Sets `FileCreateDate` from `DateTimeOriginal`. |

`Set Date from Metadata` writes `FileCreateDate` only, so sort by *Date created*
rather than *Date modified* to see the result in Explorer.

## Troubleshooting

**`exiftool not found`**
The most common problem by far, and the cause of nearly every *nothing
happens* report. Install it - see [Installing exiftool](#installing-exiftool) -
or, if it is already on the machine somewhere unusual, set `EXIFTOOL_PATH` as
described under [Configuration](#configuration). Check with `exiftool -ver` in
the same shell you launch the app from.

**Thumbnails look fine, but keywords, dates and the Scripts menu do nothing**
Same cause. The grid draws thumbnails with Pillow and never calls exiftool, so
a missing exiftool leaves the browser looking perfectly healthy while every
EXIF feature quietly fails.

**`EXIFTOOL_PATH` is set but appears to be ignored**
A configured path that does not exist is skipped deliberately, so that a stale
`.env` cannot break an otherwise working `PATH` installation. Check that the
value points at the executable itself, and do not wrap it in quotes.

**Editing `.env` changes nothing**
It is read once when the process starts. Restart the app.

**The Keys tab is empty, or images show no keywords**
The EXIF read runs in the background when you open a folder, and until it
finishes images count as unknown and stay visible rather than being filtered
out. If it never populates, press `Ctrl` `E` to re-read the folder. Failures
are not silent: a warning triangle with a count appears at the right-hand end
of the status bar, and clicking it lists what went wrong.

**The keyword panel on the right is empty**
A folder with no configured subset inherits the whole vocabulary, so in a folder
you have just opened this means the vocabulary itself is empty - nothing has
been created yet. Type a keyword into the box at the top, or use
**Manage… → Import from folder…** to adopt the keywords already on the folder's
images. If instead it emptied while you were working, you unticked everything in
**Manage…**: that is stored as the folder's subset until you leave and come
back, at which point the folder inherits the current vocabulary again.

**Keywords from a non-Latin-1 script come back as `?`**
German, French, Spanish and the rest of Western Europe round-trip correctly.
Greek, Cyrillic, Japanese and similar survive in the XMP tag but degrade to `?`
in `IPTC:Keywords`, which is a Latin-1 field unless a file declares otherwise.
Making the app declare UTF-8 there would re-encode keywords already present in
your images - turning an existing `Egypt` into `?gypten` that can no longer be
removed - so it deliberately does not. Nothing already in your files is changed
by this; only characters outside Latin-1 are affected, and only in the IPTC tag.

**A folder full of images shows an empty grid**
Only `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.gif` and `.bmp` are displayed,
and only those sitting directly in the folder - nothing recurses into
subfolders. RAW files such as `.orf` are never shown, though they do follow
their matching JPG into the trash.

## License

[MIT](LICENSE) - Copyright (c) 2026 Katja Glass Consulting.
