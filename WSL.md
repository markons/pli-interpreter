# Running the interpreter / scripts/build.py on WSL

Building a Linux binary with `scripts/build.py` needs to happen on
Linux — PyInstaller cannot cross-compile. WSL (Windows Subsystem for
Linux) is the easiest way to get a real Linux environment on a Windows
box.

## One-time setup

Recent Ubuntu/Debian refuses `pip install` into the system Python
(PEP 668, "externally-managed-environment"), so a virtual environment
is required.

```bash
sudo apt update
sudo apt install python3-venv     # needed once; python3 -m venv fails without it
```

**Use a native Linux path, not `/mnt/c/...`.** Projects living on a
Windows drive mounted into WSL via `drvfs` (i.e. anything under
`/mnt/c/`) can hit a `drvfs` permissions/symlink quirk where
`python3 -m venv` "succeeds" but silently fails to create the
`.venv/bin/pip` launcher script (the pip *library* still gets
installed under `site-packages`, just not the launcher). Cloning or
copying the repo into your WSL home directory avoids this entirely and
is noticeably faster to build in too:

```bash
mkdir -p ~/pli
cp -r "/mnt/c/Users/IF7XM6F/OneDrive - Allianz/Documents/git/pli/pli-interpreter" ~/pli/
cd ~/pli/pli-interpreter
```

(Working directly under `/mnt/c/...` also works — see the workaround
below if `pip` turns up missing.)

Create the venv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt pyinstaller
```

**If `pip` (the bare command) errors with "No such file or
directory"** after activating, the launcher script didn't get created
(the `drvfs` quirk above). Use `python3 -m pip ...` instead of `pip
...` for every install — it calls the same library directly, no
launcher script required. Existing WSL/Linux Python installs that
already have a working `pip` won't need this workaround.

## Running / building

With the venv active (`source .venv/bin/activate`):

```bash
python -m pli myprog.pli                       # run interpreted
python scripts/build.py myprog.pli [-o name]    # compile into a standalone Linux binary
./dist/myprog

python scripts/build.py                        # or: build the generic reusable interpreter
./dist/pli-<version>-linux-<arch>/pli myprog.pli
```

`scripts/build.py`'s per-program mode expands the `%` preprocessor
(`%INCLUDE`/`%DO`/`%PROC`) once at build time and embeds the resulting
source directly in the binary, so the compiled program has no runtime
dependency on the original `.pli` file(s) or their directory — see
README.md's *Building standalone executables* section for the full
CLI surface (multi-file separate compilation, `-o`, etc.).

The Db2 driver (`ibm_db`) is never bundled by either build mode
(native client library, separate redistribution terms) — sqlite-backed
`EXEC SQL` works out of the box either way.

Next time you open a new WSL terminal, you only need:

```bash
cd ~/pli/pli-interpreter        # or wherever you put it
source .venv/bin/activate
```

before running `python -m pli` / `python scripts/build.py` again — the
venv itself only needs creating once.
