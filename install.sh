#!/bin/bash
# Mispy — installer. Everything lands under your home directory; nothing needs
# sudo, and uninstall.sh removes exactly what this put there.
set -euo pipefail

LIB="$HOME/.local/lib/mispy"
BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share/mispy"
MODELS="$SHARE/models"
WHISPER="$SHARE/whisper.cpp"
CONFIG="$HOME/.config/mispy"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Kispy, the lecture-notes sibling, installs the same speech engine -- and plenty
# of people have built whisper.cpp by hand. If one is already here there is no
# reason to build it twice or download three gigabytes again.
SIBLING=""
for cand in "$HOME/.local/share/kispy" "$HOME/.local/src" "$HOME/src" "$HOME"; do
  [ -x "$cand/whisper.cpp/build/bin/whisper-cli" ] && { SIBLING="$cand"; break; }
done

MODEL="${MISPY_MODEL:-}"
SKIP_MODEL=0
for arg in "$@"; do
  case "$arg" in
    --model=*) MODEL="${arg#*=}" ;;
    --no-model) SKIP_MODEL=1 ;;
    -h|--help) printf "usage: ./install.sh [--model=large-v3|large-v3-turbo|medium|small] [--no-model]\n"; exit 0 ;;
  esac
done

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
step() { printf "\n\033[1;36m▸\033[0m \033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()  { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

printf "\n"; bold "Mispy"
printf "  records your meetings and hands you back the minutes.\n"

step "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || die "Mispy is macOS only (AVFoundation, ScreenCaptureKit, EventKit)."
major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$major" -ge 13 ] || warn "macOS 13+ is needed to record call audio; the microphone still works"
ok "macOS $(sw_vers -productVersion) on $(uname -m)"

xcode-select -p >/dev/null 2>&1 || { xcode-select --install || true; \
  die "re-run ./install.sh once the Xcode command line tools have installed"; }
ok "Xcode command line tools"

BREW="$(command -v brew || true)"
[ -n "$BREW" ] || for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$p" ] && BREW="$p"; done
[ -n "$BREW" ] || die "Homebrew is required — install it from https://brew.sh then run this again"
ok "Homebrew"

step "Command line dependencies"
need=()
command -v ffmpeg   >/dev/null || need+=(ffmpeg)     # records and mixes the tracks
command -v tectonic >/dev/null || need+=(tectonic)   # typesets the PDF
command -v cmake    >/dev/null || need+=(cmake)      # builds whisper.cpp
[ ${#need[@]} -gt 0 ] && { printf "  installing: %s\n" "${need[*]}"; "$BREW" install "${need[@]}"; }
for b in ffmpeg tectonic cmake; do command -v "$b" >/dev/null && ok "$b"; done

step "Python"
PYTHON=""
for cand in "$("$BREW" --prefix)/bin/python3.14" "$("$BREW" --prefix)/bin/python3.13" \
            "$("$BREW" --prefix)/bin/python3.12" "$("$BREW" --prefix)/bin/python3.11" \
            "$("$BREW" --prefix)/bin/python3" python3; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    PYTHON="$cand"; break
  fi
done
[ -n "$PYTHON" ] || { "$BREW" install python@3.12; PYTHON="$("$BREW" --prefix)/bin/python3.12"; }
ok "$("$PYTHON" --version) — $PYTHON"

step "Installing Mispy"
mkdir -p "$LIB" "$BIN" "$MODELS" "$CONFIG"
rm -rf "$LIB/mispy" "$LIB/style"
cp -R "$SRC/mispy" "$LIB/mispy"
cp -R "$SRC/style" "$LIB/style"
find "$LIB" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
ok "$LIB"
[ -d "$LIB/.venv" ] || "$PYTHON" -m venv "$LIB/.venv"
"$LIB/.venv/bin/pip" install --quiet --upgrade pip
"$LIB/.venv/bin/pip" install --quiet -r "$SRC/requirements.txt"
# so that the detached worker, started as `python -m`, can find the package
site="$("$LIB/.venv/bin/python" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
printf '%s\n' "$LIB" > "$site/mispy.pth"
ok "python packages"

step "Two small native helpers"
swiftc -O -o "$BIN/mispy-audio" "$SRC/swift/mispy-audio.swift"
ok "mispy-audio  records what your Mac is playing (ScreenCaptureKit)"
swiftc -O -o "$BIN/mispy-cal" "$SRC/swift/mispy-cal.swift"
ok "mispy-cal    names a meeting from your diary (EventKit)"

step "Speech recognition (whisper.cpp)"
if [ -n "$SIBLING" ] && [ ! -e "$WHISPER" ]; then
  ln -s "$SIBLING/whisper.cpp" "$WHISPER"
  ok "reusing the engine already built in ${SIBLING/#$HOME/~}"
fi
if [ ! -x "$WHISPER/build/bin/whisper-cli" ]; then
  [ -d "$WHISPER/.git" ] || git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER"
  ( cd "$WHISPER"
    cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build build -j --config Release >/dev/null )
fi
[ -x "$WHISPER/build/bin/whisper-cli" ] || die "whisper.cpp did not build"
ok "whisper-cli, running on the GPU through Metal"

if [ "$SKIP_MODEL" = "0" ]; then
  # A model the sibling already downloaded is a model we do not download again.
  for f in "$SIBLING"/models/ggml-*.bin "$SIBLING"/whisper.cpp/models/ggml-*.bin; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    [ -e "$MODELS/$base" ] || ln -s "$f" "$MODELS/$base"
  done
  [ -n "$(ls -A "$MODELS" 2>/dev/null)" ] && ok "reusing the models already on this Mac"

  if [ -z "$MODEL" ] && [ ! -e "$MODELS/ggml-large-v3.bin" ]; then
    if [ -t 0 ]; then
      printf "\n  Which model should listen to your meetings?\n\n"
      printf "    \033[1;36m1\033[0m  large-v3        best, 3.1 GB   — about 8x faster than real time\n"
      printf "    \033[1;36m2\033[0m  large-v3-turbo  1.6 GB         — nearly as good, twice as fast\n"
      printf "    \033[1;36m3\033[0m  medium          1.5 GB         — noticeably weaker on accents\n"
      printf "    \033[1;36m4\033[0m  small           0.5 GB         — only if disk space is tight\n\n"
      printf "  Your choice [1]: "; read -r choice || choice=1
      case "${choice:-1}" in 2) MODEL=large-v3-turbo ;; 3) MODEL=medium ;; 4) MODEL=small ;; *) MODEL=large-v3 ;; esac
    else
      MODEL=large-v3
    fi
  fi
  if [ -n "$MODEL" ] && [ ! -e "$MODELS/ggml-$MODEL.bin" ]; then
    printf "  downloading ggml-%s.bin — this is the long part\n" "$MODEL"
    bash "$WHISPER/models/download-ggml-model.sh" "$MODEL" "$MODELS" >/dev/null
  fi
  [ -e "$MODELS/ggml-${MODEL:-large-v3}.bin" ] && ok "ggml-${MODEL:-large-v3}.bin"
  [ -e "$MODELS/ggml-silero-v6.2.0.bin" ] || \
    bash "$WHISPER/models/download-vad-model.sh" silero-v6.2.0 "$MODELS" >/dev/null
  ok "voice activity detection (silero)"
  if [ ! -f "$CONFIG/config.toml" ] && [ -n "$MODEL" ] && [ "$MODEL" != "large-v3" ]; then
    printf '[transcribe]\nmodel = "%s"\n' "$MODELS/ggml-$MODEL.bin" > "$CONFIG/config.toml"
  fi
fi

step "Commands"
cat > "$BIN/mispy" <<EOF
#!/bin/sh
exec "$LIB/.venv/bin/python" -c '
import sys; sys.path.insert(0, "$LIB")
from mispy import cli; sys.exit(cli.main(sys.argv))
' "\$@"
EOF
chmod +x "$BIN/mispy"
ok "mispy"

case ":$PATH:" in
  *":$BIN:"*) ok "$BIN is on your PATH" ;;
  *)
    profile="$HOME/.zshrc"
    printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile"
    warn "added $BIN to your PATH in $(basename "$profile") — open a new terminal, or run:"
    printf "      export PATH=\"\$HOME/.local/bin:\$PATH\"\n"
    ;;
esac

printf "\n"; bold "Installed."
printf "\n  Now run:  \033[1;36mmispy setup\033[0m\n"
printf "  Five minutes, once. Then it is two commands a meeting.\n\n"
