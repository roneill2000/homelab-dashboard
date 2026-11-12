# Claude review: pi's downloads + search-via-debrid commits (344fa4e..d6de6b4), 11 Nov 2026

Reviewed the 15 pi commits of 11 Nov (downloads backend spec tasks + search-via-debrid steps 1-6 + audit fixes
F1/F5/F6). Secrets handling holds up (no key, link, magnet or torrent bytes reach the panel; errors are scrubbed).
Two live regressions were found and fixed by Claude (commit 5f541df, deployed 19:33Z):

1. **aria2c rejected every download** (a03a0dd): `--max-redirect=0` is a wget option; aria2c 1.37.0 exits
   "unrecognized option" before fetching. Every download since the 18:50Z deploy would have failed at start.
   Flag dropped (resolve_redirects already hands aria2c the final URL). New test runs the installed aria2c with
   `--dry-run` over ARIA2_FLAGS. Lesson: the RunHardening tests mock subprocess, so nothing ever ran aria2c.
2. **Usenet Get 503'd with a key file present** (28856b7): every search Get went to grab_debrid, which only
   understands torrents ("the indexer did not give a usable torrent"). RESULTS now keeps `protocol`; only
   torrent results go to Real-Debrid/TorBox. Test `test_a_usenet_get_goes_to_prowlarr_even_with_both_keys`.

## Open (not fixed; for pi, decisions for Ryan marked)

3. **F2 fixed by 776c28f (11 Nov): ** an archive answer after a file was already handed over in the same tick
   keeps that file's row (only the packed file is skipped), guarded by the test
   `test_a_packed_answer_after_a_started_file_keeps_that_file_and_skips_only_the_packed_one`. Marked here as
   still open when the review was written; the same-tick case passes now (checked 12 Nov 2026 by pi).
4. **Same orphan via Cancel (new):** the tick copies the row, hands files to the worker, then `_prep_update`
   returns False if Cancel removed the row meanwhile, and `add_rows` are dropped. Downloads run with no row.
   Fix: in `_prep_update`, when the row is gone but `add_rows` exist, still add them (or stop those units).
5. **Keys are never re-read once loaded (new):** `_rd_key`/`_torbox_key` refresh only while the cached value
   is None; the docstrings say "re-read after 60 s". Rotating the TorBox key (planned) needs a
   `homelab-dashboard` restart on LXC 104 until this re-reads by mtime.
6. **F3:** `files_cut` / `files_small` never reach the panel (low).
7. A media file that keeps failing hand-over leaves the row 'preparing' until the 24 h clock closes it as
   "did not finish this torrent in 24 hours", which is misleading (low).
8. `_pace` globals are shared by the tick thread and the HTTP retry thread without a lock (low).
9. **Ryan to decide:** Real-Debrid code 33 "Torrent already active" counts as a refusal and sends a second copy
   to TorBox.

Also deployed with this review: broker loop guard (4e0e125), see CLAUDE.md "Loop guard".
