# Resume

The resume PDF is **never committed** (see `.gitignore`).

- Locally: copy your PDF to `profile/resume/resume.pdf`.
- GitHub Actions: store it base64-encoded in the secret `RESUME_PDF_B64`
  (`scripts/encode_resume.ps1` / `scripts/encode_resume.sh` produce the string); the workflow decodes it to
  `profile/resume/resume.pdf` at runtime.
