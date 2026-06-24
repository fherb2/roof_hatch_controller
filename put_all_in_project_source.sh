#!/usr/bin/env bash
# ==============================================================================
# put_all_in_project_source_file.sh
#
# Collects source files from ./source into a single project_source.txt.
# Each file is wrapped in metadata lines (prefix #!PKSRC) that carry:
#   pksrc_ts   — timestamp of this script run, identical for every file block
#   file_mtime — last modification time of the individual source file
#
# The #!PKSRC prefix does not appear in normal Python or CUDA source code,
# so these lines are unambiguous metadata even when embedded in a search index.
#
# USAGE
#   ./put_all_in_project_source_file.sh [-h] [-md] [-txt]
#
# OPTIONS
#   -h    Print this help and exit. No output file is written.
#   -md   Also include .md files for this run (temporary, not saved to config).
#   -txt  Also include .txt files for this run (temporary, not saved to config).
#
# OUTPUT
#   ./project_source.txt — created or overwritten on each run.
#
# CONFIGURATION
#   Edit BASE_EXTENSIONS and EXCLUDE_DIRS in the section below.
# ==============================================================================


# ==============================================================================
# CONFIGURATION — edit this section as needed
# ==============================================================================

# BASE_EXTENSIONS
#   File extensions included in EVERY run.
#   Write without the leading dot, one entry per array element.
#   To add C headers for example: BASE_EXTENSIONS=("py" "cu" "h")
BASE_EXTENSIONS=("py" "html" "json")

# EXCLUDE_DIRS
#   Directory *names* (not full paths) to skip at any depth under ./source.
#   The match is on the bare name, so "backup" prunes every directory called
#   "backup" no matter where it appears in the tree.
#   To also skip ".git":  EXCLUDE_DIRS=("backup" "__pycache__" ".git")
EXCLUDE_DIRS=("backup" "__pycache__" "static")


# ==============================================================================
# DO NOT EDIT BELOW THIS LINE
# ==============================================================================

usage() {
    cat <<'EOF'
USAGE
  ./put_all_in_project_source_file.sh [-h] [-md] [-txt]

OPTIONS
  -h    Print this help and exit. No output file is written.
  -md   Also include .md  files for this run (not saved to BASE_EXTENSIONS).
  -txt  Also include .txt files for this run (not saved to BASE_EXTENSIONS).

OUTPUT
  ./project_source.txt — created or overwritten on each run.

CONFIGURATION
  Edit BASE_EXTENSIONS and EXCLUDE_DIRS near the top of this script.
  BASE_EXTENSIONS : always included file suffixes (without dot).
  EXCLUDE_DIRS    : directory names pruned at any depth under ./source.
EOF
}

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
EXTRA_EXTENSIONS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h)
            usage
            exit 0
            ;;
        -md)
            EXTRA_EXTENSIONS+=("md")
            ;;
        -txt)
            EXTRA_EXTENSIONS+=("txt")
            ;;
        *)
            echo "Error: unknown option '$1'" >&2
            echo "" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

# Merge base extensions with any extras requested on the command line
ACTIVE_EXTENSIONS=("${BASE_EXTENSIONS[@]}" "${EXTRA_EXTENSIONS[@]}")

# --------------------------------------------------------------------------
# Build find(1) argument arrays dynamically
# --------------------------------------------------------------------------

# Prune conditions from EXCLUDE_DIRS:
#   yields:  ( -name "backup" -o -name "__pycache__" )  -prune  -o
PRUNE_CONDS=()
for dir in "${EXCLUDE_DIRS[@]}"; do
    [[ ${#PRUNE_CONDS[@]} -gt 0 ]] && PRUNE_CONDS+=(-o)
    PRUNE_CONDS+=(-name "$dir")
done

# Name conditions from ACTIVE_EXTENSIONS:
#   yields:  ( -name "*.py" -o -name "*.cu" )
NAME_CONDS=()
for ext in "${ACTIVE_EXTENSIONS[@]}"; do
    [[ ${#NAME_CONDS[@]} -gt 0 ]] && NAME_CONDS+=(-o)
    NAME_CONDS+=(-name "*.${ext}")
done

# Wrapper: assembles and runs the find command from the arrays above.
# Omits the prune clause entirely when EXCLUDE_DIRS is empty.
run_find() {
    local args=("./source")
    if [[ ${#PRUNE_CONDS[@]} -gt 0 ]]; then
        args+=('(' "${PRUNE_CONDS[@]}" ')' -prune -o)
    fi
    args+=(-type f '(' "${NAME_CONDS[@]}" ')' -print)
    find "${args[@]}"
}

# --------------------------------------------------------------------------
# Generation timestamp — single value used throughout the output file
# --------------------------------------------------------------------------
PKSRC_TS=$(date +"%Y-%m-%d_%H-%M-%S")
echo "# project_source.txt generation started: ${PKSRC_TS}"

# --------------------------------------------------------------------------
# Write project_source.txt
# --------------------------------------------------------------------------
{
    # Global header with instructions for Claude
    echo "#!PKSRC:HEADER:BEGIN | project_source.txt | pksrc_ts: ${PKSRC_TS}"
    echo "#"
    echo "# @Claude: Das File wurde mit dem Zeitstempel ${PKSRC_TS} erstellt."
    echo "# Gib diesen Zeitstempel bitte im Chat immer kurz als Referenz mit aus,"
    echo "# wenn Du in Deiner Antwort Ergebnisse einer Suche in diesem File hast."
    echo "# Jede enthaltene Quelldatei hat eine Einleitungszeile mit dem Token"
    echo "# 'pksrc_ts: ${PKSRC_TS}'. Falls ein Suchergebnis einen anderen pksrc_ts-Wert"
    echo "# zeigt, ist es ein veraltetes Ergebnis aus einem früheren Index-Stand —"
    echo "# weise bitte ausdrücklich darauf hin."
    echo "# Das Feld 'file_mtime' gibt den letzten Änderungszeitpunkt der Quelldatei an."
    echo "# Anhand von file_mtime erkennst Du, ob eine Datei bei einem bestimmten"
    echo "# Arbeitsschritt tatsächlich angefasst wurde oder nicht."
    echo "#"
    echo "#!PKSRC:HEADER:END"

    # One block per source file, sorted by path
    run_find | sort | while read -r f; do
        FILE_MTIME=$(date -d "$(stat -c "%y" "$f")" +"%Y-%m-%d_%H-%M-%S")
        echo ""
        echo "#!PKSRC:FILE:BEGIN | ${f} | pksrc_ts: ${PKSRC_TS} | file_mtime: ${FILE_MTIME}"
        cat "$f"
        echo ""
        echo "#!PKSRC:FILE:END | ${f}"
    done

} > project_source.txt

echo "# project_source.txt written successfully. Timestamp: ${PKSRC_TS}"
