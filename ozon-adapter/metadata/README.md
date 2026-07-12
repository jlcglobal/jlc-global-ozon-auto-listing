# Ozon Metadata Snapshots

This directory contains immutable marketplace metadata snapshots imported from
trusted exports. Each snapshot is stored once under its version name and is
addressed by the `(categoryId, typeId)` pair.

The snapshots are offline decision data. Live Ozon metadata remains
authoritative for attribute types, dictionary identifiers and allowed values.
Product directories must reference a snapshot version instead of copying the
whole database.
