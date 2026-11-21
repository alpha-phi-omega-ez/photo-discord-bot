r"""Cross-phase ledger of bugs pinned by Phase 1 tests.

Each entry below is pinned with ``pytest.mark.xfail(strict=True)`` so an
unexpected pass fails loudly; the phase that fixes the bug removes the
marker (and re-checks the expectation) in the same change. Line numbers
drift as phases land - anchors are function names, not offsets.

Pinned with tests today (all -> fixed in Phase 3):

- ``convert_to_jpeg``: ``.heif`` uploads keep the ``.heif`` filename while
  the content is already JPEG (the filename replace only knows ``heic``).
- ``convert_to_jpeg``: ``str.replace("heic", "jpeg")`` mangles filenames
  whose stem contains the substring ``heic``, not just the extension.
- ``find_file_name``: ``IMAGE_NAME_PATTERN`` / ``VIDEO_NAME_PATTERN`` use
  ``[\w]+``, so hyphens and spaces in filenames are dropped from the
  extracted name.

Contract pin (not a bug, documents caller behavior - Phase 3 may revisit
when fixing the regex):

- ``find_file_name`` only matches lowercase extensions; callers must pass
  a lowercased URL (``url_lower``).

Known gaps with no Phase 1 test yet; the phase touching that code must
add the pin first:

- Invalid MIME types accepted for video uploads, e.g. ``video/mov``
  (``upload``) -> Phase 3.
- ``finally`` block containing a ``return`` swallows upload failures in
  the disk-backed video path (``download_video``) -> Phase 3.
- Missing ``requests`` timeouts let downloads hang the single worker
  (``get_file_size``, ``download_image``, ``download_video``) -> Phase 2.
"""
