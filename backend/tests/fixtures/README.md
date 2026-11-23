# Test fixtures

Smoke-test inputs for the Round 3 features. Generate them once from `backend/`:

    python -m tests.fixtures.make_fixtures

- `scanned_sample.pdf`: image-only PDF. Upload it to test OCR (Improvement 1). The
  findings should include the superlative, the banned term, the expired offer, and the price.
- `off_brand_logo.png`: upload it to test the vision check (Improvement 2) once an approved
  logo has been uploaded in Brand Knowledge Base.

The unit tests do not use these files; they create their own images in a temp folder.
