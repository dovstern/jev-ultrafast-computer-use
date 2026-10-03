#!/bin/sh
# Save the TypeSafe key where the plugin launcher reads it. Uses $TYPESAFE_API_KEY when
# exported, otherwise prompts with hidden input. Run it in a terminal, not through an agent.
set -eu
key_file="${XDG_CONFIG_HOME:-$HOME/.config}/jev/env"
key="${TYPESAFE_API_KEY:-}"
if [ -z "$key" ]; then
  printf 'TypeSafe API key (from https://console.typesafe.ai/): ' >&2
  if [ -t 0 ]; then
    trap 'stty echo' EXIT
    stty -echo
  fi
  IFS= read -r key || true
  [ -t 0 ] && printf '\n' >&2
fi
# The launcher sources this file, so refuse anything that could not sit inside single quotes.
case "$key" in
  '' | *[[:space:]\']*) echo "Key is empty or contains a space or quote; nothing saved." >&2; exit 1 ;;
esac
umask 077
mkdir -p "$(dirname "$key_file")"
# Write a fresh private file beside the old one, keeping its other settings, then swap it in:
# umask does not tighten an existing file's mode, and mv also replaces a symlink instead of following it.
tmp="$key_file.$$"
{ grep -v '^TYPESAFE_API_KEY=' "$key_file" 2>/dev/null || true; printf "TYPESAFE_API_KEY='%s'\n" "$key"; } > "$tmp"
mv "$tmp" "$key_file"
echo "Saved to $key_file. Reconnect the jev-browser server to use it." >&2
