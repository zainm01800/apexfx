# Frozen Book R stop-study input package

The `chunk-000` through `chunk-040` files are ordered binary chunks of
`book_r_stop_inputs_2026-09-03.parquet`. The audit runner concatenates them in
filename order and refuses to load the result unless its SHA-256 is:

`efc75fb7056efe2d03d0cd13de955616882c2c7c54ac794c53cdfdbac0cc7974`

The 16,000-byte split packaging exists only to avoid the repository host's
broken handling of larger Git objects. It does not transform the parquet data.
