---
name: pdf-renderer
description: >-
  Renders PDF files as viewable HTML artifacts with page-by-page image display.
  Use when the user asks to view, display, open, read, render, or show a PDF
  file, or when a task involves inspecting PDF contents visually.
---

# PDF Renderer

Renders a PDF file as a multi-page HTML artifact the user can view in the side
pane. Always links back to the original PDF file.

## Prerequisites

`pdftoppm` from poppler must be available.

- macOS: `brew install poppler`
- Ubuntu/Debian: `sudo apt-get install poppler-utils`

If it is missing, tell the user and offer to install it.

## Steps

1. **Get page count** to plan the layout:

   ```bash
   pdfinfo "<path-to-pdf>" | grep Pages
   ```

2. **Convert pages to PNG** at 200 DPI into a scratch directory:

   ```bash
   mkdir -p "<artifact_dir>/scratch/pdf_pages"
   pdftoppm -png -r 200 "<path-to-pdf>" "<artifact_dir>/scratch/pdf_pages/page"
   ```

3. **Build a self-contained HTML artifact** using a Python script that
   base64-encodes each page PNG directly into `<img>` data URIs.

   Relative `src` paths do NOT work in the artifact iframe. You MUST use
   `data:image/png;base64,...` URIs for the images.

   Use a script like this (adapt paths as needed):

   ```python
   import base64, pathlib

   artifact_dir = "<artifact_dir>"
   pages_dir = pathlib.Path(artifact_dir) / "scratch" / "pdf_pages"
   original_path = "<absolute-path-to-original-pdf>"
   filename = pathlib.Path(original_path).name

   page_files = sorted(pages_dir.glob("page-*.png"))
   total = len(page_files)

   page_blocks = []
   for i, pf in enumerate(page_files, 1):
       b64 = base64.b64encode(pf.read_bytes()).decode()
       page_blocks.append(
           '<div class="bg-[var(--card)] border border-[var(--border)] '
           'rounded-xl p-4 shadow-sm">'
           '<p class="text-[var(--muted-foreground)] text-xs mb-2">'
           f'Page {i} of {total}</p>'
           f'<img src="data:image/png;base64,{b64}" alt="Page {i}" '
           'class="w-full rounded" /></div>'
       )

   html = f"""<!DOCTYPE html>
   <html>
   <head>
     <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
   </head>
   <body class="bg-[var(--background)] text-[var(--foreground)] antialiased p-6">
     <div class="max-w-4xl mx-auto space-y-6">
       <div class="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 shadow-sm">
         <h1 class="text-xl font-semibold">{filename}</h1>
         <p class="text-[var(--muted-foreground)] text-sm mt-1">{total} pages</p>
         <p class="text-[var(--muted-foreground)] text-sm mt-1">
           Original file:
           <a href="file://{original_path}"
              class="text-[var(--primary)] underline">{filename}</a>
         </p>
       </div>
       {"".join(page_blocks)}
     </div>
   </body>
   </html>"""

   out = pathlib.Path(artifact_dir) / "pdf_viewer.html"
   out.write_text(html)
   ```

4. **Write the artifact** using `write_to_file` with `Overwrite: true` and
   `ArtifactMetadata` set to `UserFacing: true`.

5. **Reference the original PDF** in your chat response using a markdown file
   link:

   ```
   Original PDF: [filename.pdf](file:///absolute/path/to/filename.pdf)
   ```

## Important

- Relative image paths (`./pdf_pages/page-1.png`) do NOT work in the artifact
  iframe due to CSP and sandboxing. Always base64-encode images as data URIs.
- Make the artifact standalone (not inline) since PDFs are typically tall.
- For very large PDFs (50+ pages), warn the user that the artifact will be
  large and consider rendering only a subset of pages.
