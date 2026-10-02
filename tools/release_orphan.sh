#!/bin/bash
# Build a publishable ORPHAN branch from this plugin's tree at a named commit.
#
# WHY THIS EXISTS. A push publishes the HISTORY, not the tree. This package is developed inside
# another repository, and git itself writes that repository's absolute path into every
# auto-generated merge subject — nobody types those, which is exactly why no amount of care in the
# working tree catches them. Rewriting a long history to scrub them is a large, error-prone act
# whose result is still a history nobody reads. So a release is published as ONE COMMIT with NO
# ancestry: a fresh repository, the squashed tree, `publish_check.py` run against it as a git root
# (so the tags, ignore and history halves all actually run), and the push command PRINTED for a
# person to run. This script pushes nothing, touches no existing clone, and writes only inside a
# directory it created.
#
#   bash tools/release_orphan.sh --sha <commit> [--repo DIR] [--subtree PATH]
#                                [--branch NAME] [--out DIR] [--remote URL] [--tag NAME]
#                                [--with-excluded]
#
#   --sha      REQUIRED. The commit in the development repository whose tree is published.
#   --repo     the development repository (default: the one this script lives in).
#   --subtree  the plugin's path inside it (default: derived from this script's location; pass "."
#              when the plugin IS the repository root).
#   --branch   the orphan branch's name (default: public-YYYY-MM-DD).
#   --out      where to build (default: a fresh directory under the system temp dir). Must not
#              exist, or must be empty.
#   --remote   printed in the push command instead of the placeholder. Never contacted.
#   --with-excluded  publish the development-only files too. By default every path listed in the
#              tree's `rules/publish-exclude.json` is left out of the build, and the build is
#              refused if one of them is found in it afterwards.
#   --tag      the tag you mean to publish. Optional: the tag is ALWAYS `v` + the `version` in the
#              tree's `.claude-plugin/plugin.json`, and a --tag that differs is refused before
#              anything is built. Bump the version (and give CHANGELOG.md its heading) instead.
#
# THE VERSION RULE. tag = plugin.json version, always. Patch (0.5.0 -> 0.5.1) for fixes and
# polish; minor (0.5 -> 0.6) for a capability a user notices; major only for a rework or a
# complete new feature set. A tree whose CHANGELOG.md has no `## <version> ` heading is refused:
# a release names what changed.
#
# Exit codes: 0 built and clean · 1 usage/environment, or the tag/version/changelog disagree ·
# 2 publish check REFUSED the squashed tree.
set -uo pipefail

die() { printf 'release_orphan: %s\n' "$*" >&2; exit 1; }

here="$(cd -- "$(dirname -- "$0")" && pwd)"
plugin_dir="$(cd -- "$here/.." && pwd)"

sha=""; repo=""; subtree=""; branch=""; out=""; tag=""; remote="<the repository's URL>"; with_excluded=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sha)     sha="${2:-}"; shift 2 || die "--sha needs a value" ;;
    --repo)    repo="${2:-}"; shift 2 || die "--repo needs a value" ;;
    --subtree) subtree="${2:-}"; shift 2 || die "--subtree needs a value" ;;
    --branch)  branch="${2:-}"; shift 2 || die "--branch needs a value" ;;
    --out)     out="${2:-}"; shift 2 || die "--out needs a value" ;;
    --remote)  remote="${2:-}"; shift 2 || die "--remote needs a value" ;;
    --tag)     tag="${2:-}"; shift 2 || die "--tag needs a value" ;;
    --with-excluded) with_excluded=1; shift ;;
    -h|--help) sed -n '2,39p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$sha" ] || die "--sha is required: name the commit whose tree you are publishing"

if [ -z "$repo" ]; then
  repo="$(git -C "$plugin_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not find a git repository above $plugin_dir — pass --repo"
fi
repo="$(cd -- "$repo" && pwd)" || die "--repo is not a directory"

if [ -z "$subtree" ]; then
  # The plugin's path RELATIVE to the repository, derived rather than spelled out: the same script
  # has to work from a checkout, a worktree, and a copy at the repository root.
  case "$plugin_dir" in
    "$repo") subtree="." ;;
    "$repo"/*) subtree="${plugin_dir#$repo/}" ;;
    *) die "$plugin_dir is not inside $repo — pass --subtree" ;;
  esac
fi

# `--subtree plugin/` (what tab completion gives) names the same tree as `plugin`. Left as typed,
# every path built from it carried `//`, git found none of them, and the build came out
# unversioned with nothing left out.
while [ "$subtree" != "." ] && [ "$subtree" != "${subtree%/}" ]; do subtree="${subtree%/}"; done
[ -n "$subtree" ] || die "--subtree is empty"

full_sha="$(git -C "$repo" rev-parse --verify "${sha}^{commit}" 2>/dev/null)" \
  || die "$sha is not a commit in $repo"

if [ "$subtree" = "." ]; then spec="$full_sha"; else spec="$full_sha:$subtree"; fi
# NOTE: `<commit>:<path>^{tree}` is NOT a valid revision — it fails for a path that is really
# there, which read as "the subtree is missing" the first time this ran. Ask what the object IS.
spec_type="$(git -C "$repo" cat-file -t "$spec" 2>/dev/null)" \
  || die "$subtree does not exist at $full_sha"
case "$spec_type" in
  tree|commit) : ;;
  *) die "$spec is a $spec_type, not a tree" ;;
esac

# THE VERSION GATE, before anything is built. Read out of the COMMIT, not the working copy: the
# version that ships is the one in the tree being published.
if [ "$subtree" = "." ]; then vpath=""; else vpath="$subtree/"; fi
nl=$'\n'
version="$(git -C "$repo" show "$full_sha:${vpath}.claude-plugin/plugin.json" 2>/dev/null | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("version", "") or "")
except Exception:
    print("")
' 2>/dev/null)"
if [ -z "$version" ]; then
  [ -z "$tag" ] || die "REFUSED: --tag $tag, but the tree carries no version in .claude-plugin/plugin.json"
  version="unversioned"
else
  if [ -n "$tag" ] && [ "$tag" != "v$version" ]; then
    die "REFUSED: --tag $tag differs from plugin.json's version $version (the tag must be v$version). Bump the version instead."
  fi
  tag="v$version"
  # A versioned tree with NO CHANGELOG.md is refused too: a missing file has no heading.
  changelog="$(git -C "$repo" show "$full_sha:${vpath}CHANGELOG.md" 2>/dev/null)" || changelog=""
  # `case`, not `grep -q`: under pipefail an early-exiting grep can fail the pipe on a MATCH.
  case "$nl$changelog" in
    *"$nl## $version "*) : ;;
    *) die "REFUSED: CHANGELOG.md has no '## $version ' heading — give the release its dated heading first" ;;
  esac
fi

[ -n "$branch" ] || branch="public-$(date +%Y-%m-%d)"

if [ -z "$out" ]; then
  out="$(mktemp -d "${TMPDIR:-/tmp}/gedaechtnis-release.XXXXXX")" || die "could not create a build directory"
else
  if [ -e "$out" ]; then
    [ -d "$out" ] || die "$out exists and is not a directory"
    [ -z "$(ls -A -- "$out" 2>/dev/null)" ] || die "$out is not empty — refusing to build over it"
  else
    mkdir -p -- "$out" || die "could not create $out"
  fi
  out="$(cd -- "$out" && pwd)"
fi

printf 'release_orphan: building %s\n' "$branch"
printf '  version       : %s\n' "$version"
printf '  source commit : %s\n' "$full_sha"
printf '  source subtree: %s\n' "$subtree"
printf '  build dir     : %s\n' "$out"

# THE LEAVE-OUT LIST, read out of the COMMIT like the version. One path per line; a list that
# cannot be read, or a path that is absolute, climbs with `..`, or holds anything but letters,
# digits, `.`, `_`, `-` and `/`, refuses the build: a list that is half understood leaves out half.
excluded=""
if [ "$with_excluded" -eq 0 ]; then
  if git -C "$repo" cat-file -e "$full_sha:${vpath}rules/publish-exclude.json" 2>/dev/null; then
    excluded="$(git -C "$repo" show "$full_sha:${vpath}rules/publish-exclude.json" | python3 -c '
import json, re, sys
try:
    paths = json.load(sys.stdin)["exclude"]
except Exception:
    sys.exit(1)
if not isinstance(paths, list):
    sys.exit(1)
for p in paths:
    # `if`, not `assert`: PYTHONOPTIMIZE strips an assert, and this is the check.
    if not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", p) or p.startswith("-"):
        sys.exit(1)
    if any(seg in ("", ".", "..") for seg in p.split("/")):     # absolute, trailing slash, x//y, climbs
        sys.exit(1)
for p in paths:
    print(p)
')" || die "REFUSED: rules/publish-exclude.json could not be read as {\"exclude\": [paths]}"
  fi
fi

# A listed path that is not in the tree is refused: a name that matches nothing (a typo, a file
# renamed since) would leave nothing out and still be counted as left out.
old_ifs="$IFS"; IFS="$nl"
for p in $excluded; do
  git -C "$repo" cat-file -e "$full_sha:${vpath}$p" 2>/dev/null \
    || die "REFUSED: $p is on the leave-out list (rules/publish-exclude.json) and is not in the tree at $full_sha — correct the list"
done
IFS="$old_ifs"

# The tree comes out of git, not out of the working copy: whatever is untracked, ignored or
# half-edited on this machine is not part of a release by construction.
set -- .
old_ifs="$IFS"; IFS="$nl"
for p in $excluded; do set -- "$@" ":(exclude)$p"; done
IFS="$old_ifs"
git -C "$repo" archive --format=tar "$spec" -- "$@" | (cd -- "$out" && tar -xf -) \
  || die "could not extract $spec"

# Checked on the result, not assumed from the command: a listed path found in the build refuses it.
left_out=0
old_ifs="$IFS"; IFS="$nl"
for p in $excluded; do
  [ ! -e "$out/$p" ] || die "REFUSED: $p is on the leave-out list and is in the build"
  left_out=$((left_out + 1))
done
IFS="$old_ifs"
if [ "$with_excluded" -eq 1 ]; then
  printf '  left out      : nothing (--with-excluded)\n'
else
  printf '  left out      : %s path(s) named in rules/publish-exclude.json\n' "$left_out"
fi

[ -n "$(ls -A -- "$out")" ] || die "the extracted tree is empty"

# A published repository needs its own ignore file; the parent repository's does not travel with a
# subtree. Written only if the tree does not already carry one.
if [ ! -e "$out/.gitignore" ]; then
  printf '__pycache__/\n*.pyc\n.pytest_cache/\n' > "$out/.gitignore"
fi

git -C "$out" init --quiet || die "git init failed in $out"
git -C "$out" symbolic-ref HEAD "refs/heads/$branch" || die "could not name the branch $branch"
# A fixed machine identity: a release commit is not a person, and an author line is history too.
git -C "$out" config user.name  "Gedaechtnis"
git -C "$out" config user.email "gedaechtnis@local"
git -C "$out" config commit.gpgsign false


# NO PATH IN THIS MESSAGE, and no development-repository name: the message is history, and history
# is what this whole script exists to keep clean.
# Written OUTSIDE the tree being published: a file staged by accident is a file published.
msg_file="$(mktemp "${TMPDIR:-/tmp}/gedaechtnis-release-msg.XXXXXX")" || die "could not write the commit message"
{
  printf 'Das Gedaechtnis %s — published tree\n\n' "$version"
  printf 'One commit, no ancestry. This repository publishes the plugin tree as it stood at a\n'
  printf 'single point; the development history it was cut from is not published, and each\n'
  printf 'release is a new orphan commit rather than a continuation of the last.\n\n'
  printf 'What changed, and what each change means for someone running it, is in CHANGELOG.md.\n'
} > "$msg_file"

git -C "$out" add --all -- . || die "could not stage the tree"
git -C "$out" commit --quiet -F "$msg_file" || die "the release commit failed"
# The message file is left where mktemp put it, under the system temp directory: nothing in this
# package calls `rm`, and a temp file is the operating system's to collect.

commit="$(git -C "$out" rev-parse --short HEAD)"
files="$(git -C "$out" ls-files | wc -l | tr -d ' ')"
printf 'release_orphan: %s committed as %s (%s file(s))\n' "$branch" "$commit" "$files"

# THE GATE. Run from inside the squashed tree, whose root IS a git root — so the tags,
# tracked-but-ignored and history halves run rather than being silently skipped, which is the
# whole reason a subdirectory check is not good enough here.
check="$out/tools/publish_check.py"
[ -f "$check" ] || die "the published tree carries no tools/publish_check.py — refusing to bless it"
printf 'release_orphan: publish check over the squashed tree:\n'
check_out="$(cd -- "$out" && python3 tools/publish_check.py --root . 2>&1)"
check_rc=$?
printf '%s\n' "$check_out"
if [ "$check_rc" -ne 0 ]; then
  printf 'release_orphan: REFUSED — the squashed tree did not pass its own publish check.\n' >&2
  printf 'release_orphan: nothing was pushed. The build is left at %s for you to read.\n' "$out" >&2
  exit 2
fi

if [ "$version" != "unversioned" ]; then
  # Cut only after the publish check passed. A local tag only; it travels when a person pushes it BY NAME.
  git -C "$out" tag "$tag" || die "could not tag $tag"
fi

cat <<EOF

release_orphan: the tree is clean and the branch is built. NOTHING HAS BEEN PUSHED.

To publish it, run these two commands yourself:

    git -C $out remote add origin $remote
    git -C $out push origin $branch

Then, on the hosting side: make $branch the repository's default branch (or merge it), and
delete the branch it replaced when you are satisfied with what is there.
EOF
if [ "$version" != "unversioned" ]; then
  cat <<EOF
The tag $tag is cut locally and equals plugin.json's version. Push it BY NAME, never with --tags:

    git -C $out push origin $tag

EOF
fi
exit 0
