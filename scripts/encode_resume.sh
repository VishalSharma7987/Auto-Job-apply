#!/usr/bin/env sh
# Base64-encodes your resume PDF for the GitHub secret RESUME_PDF_B64.
# Usage: ./scripts/encode_resume.sh path/to/resume.pdf   (writes resume.b64.txt - git-ignored; delete after use)
set -eu
[ $# -eq 1 ] || { echo "usage: $0 resume.pdf" >&2; exit 2; }
base64 < "$1" | tr -d '\n' > resume.b64.txt
echo "Wrote resume.b64.txt ($(wc -c < resume.b64.txt) chars). Paste its content into the RESUME_PDF_B64 secret, then delete the file."
