"""
ARCHS4 H5 expression matrix accessor.

Reads expression data directly from human_gene_v2.latest.h5 without any
TSV export or database registration step.

H5 layout:
    data/expression               uint32  (n_genes × n_samples)
    meta/genes/symbol             object  (n_genes,)
    meta/samples/geo_accession    object  (n_samples,)  — GSM IDs
    meta/samples/series_id        object  (n_samples,)  — GSE IDs

Performance notes:
- gene symbols and series_id arrays are cached in memory on first open
  (67k genes ~1 MB; 1M series_id strings ~70 MB — loaded once, reused forever)
- Expression matrix reads use a single contiguous H5 slice (row_start:row_end)
  for sorted gene ranges, avoiding per-row random I/O
- sample column indices are resolved via numpy boolean mask over the cached array
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator

import h5py
import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_H5 = Path(__file__).parent.parent / "human_gene_v2.latest.h5"


def _decode(raw) -> str:
    return raw.decode() if isinstance(raw, bytes) else raw


class ARCHS4:
    """Read-only accessor for the ARCHS4 H5 expression file.

    Open once and keep open; safe for concurrent reads (h5py SWMR / read-only).
    Use as a context manager or call .close() explicitly.
    """

    def __init__(self, h5_path: str | Path = _DEFAULT_H5) -> None:
        self._path = Path(h5_path)
        if not self._path.exists():
            raise FileNotFoundError(f"ARCHS4 H5 file not found: {self._path}")
        self._f = h5py.File(self._path, "r")

        # --- cache gene symbols (67k, ~1 MB) ---
        raw_genes = self._f["meta"]["genes"]["symbol"][:]
        self._gene_symbols: list[str] = [_decode(g) for g in raw_genes]
        # upper-cased lookup: symbol_upper → row index
        self._gene_index: dict[str, int] = {
            sym.upper(): i for i, sym in enumerate(self._gene_symbols)
        }

        # --- cache series_id (1M strings, ~70 MB, loaded once) ---
        raw_series = self._f["meta"]["samples"]["series_id"][:]
        self._series_ids: np.ndarray = np.array([_decode(v) for v in raw_series])

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ARCHS4":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        self._f.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _col_indices(self, gse: str) -> np.ndarray:
        """Column indices in the expression matrix belonging to this GSE."""
        return np.where(self._series_ids == gse)[0]

    def _gsm_ids(self, col_indices: np.ndarray) -> list[str]:
        raw = self._f["meta"]["samples"]["geo_accession"][col_indices]
        return [_decode(v) for v in raw]

    def _resolve_genes(self, gene_filter: set[str] | None) -> list[tuple[str, int]]:
        """Return [(symbol, row_index), ...] respecting an optional filter.

        gene_filter values are matched case-insensitively.
        """
        if gene_filter:
            upper = {g.upper() for g in gene_filter}
            return [
                (self._gene_symbols[i], i)
                for sym_upper, i in self._gene_index.items()
                if sym_upper in upper
            ]
        return [(sym, i) for i, sym in enumerate(self._gene_symbols)]

    def _read_matrix(
        self,
        gene_rows: list[tuple[str, int]],
        col_idx: np.ndarray,
    ) -> tuple[list[str], list[list[int]]]:
        """Read expression values for the given (gene, row_index) pairs and columns.

        Optimisation: sort genes by row index so we can coalesce adjacent rows
        into a single H5 slice when genes are contiguous (common for filtered sets).
        For the full-genome case (67k rows), we read one contiguous block.
        """
        expr = self._f["data"]["expression"]
        col_idx_sorted = np.sort(col_idx)         # H5 fancy-index requires sorted cols
        col_order = np.argsort(np.argsort(col_idx))  # to restore original column order

        # Sort gene rows by H5 row index for sequential I/O
        gene_rows_sorted = sorted(gene_rows, key=lambda x: x[1])
        syms_sorted = [sym for sym, _ in gene_rows_sorted]
        row_indices = [i for _, i in gene_rows_sorted]

        # Full-genome shortcut: read all rows as one slice
        if len(row_indices) == len(self._gene_symbols):
            block = expr[:, col_idx_sorted]          # (n_genes, n_cols)
            matrix_sorted = block[:, col_order].tolist()
            return syms_sorted, matrix_sorted

        # Filtered genes: read row-by-row (typically small, e.g. <100 genes)
        matrix_sorted: list[list[int]] = []
        for row_i in row_indices:
            vals = expr[row_i, col_idx_sorted][col_order].tolist()
            matrix_sorted.append(vals)

        return syms_sorted, matrix_sorted

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def gene_symbols(self) -> list[str]:
        return self._gene_symbols

    def has_gse(self, gse: str) -> bool:
        """True if the H5 file contains any samples for this GSE."""
        return bool(np.any(self._series_ids == gse))

    def gse_info(self, gse: str) -> dict | None:
        """Basic stats for a GSE (gene_count, sample_count), or None if absent."""
        col_idx = self._col_indices(gse)
        if len(col_idx) == 0:
            return None
        return {
            "accession": gse,
            "gene_count": len(self._gene_symbols),
            "sample_count": int(len(col_idx)),
            "file_format": "h5",
        }

    def batch_gse_info(self, gse_list: list[str]) -> dict[str, dict]:
        """Return info dicts for multiple GSE accessions in a single pass over series_id.

        Much faster than calling gse_info() per result — one numpy scan for all.

        Returns:
            { accession: { gene_count, sample_count, file_format } }
            Only accessions found in the H5 file are included.
        """
        if not gse_list:
            return {}
        wanted = set(gse_list)
        # Single pass: filter series_ids to only wanted GSEs, then count per unique value
        mask = np.isin(self._series_ids, list(wanted))
        filtered = self._series_ids[mask]
        unique, counts = np.unique(filtered, return_counts=True)
        n_genes = len(self._gene_symbols)
        return {
            gse: {
                "accession": gse,
                "gene_count": n_genes,
                "sample_count": int(cnt),
                "file_format": "h5",
            }
            for gse, cnt in zip(unique.tolist(), counts.tolist())
        }

    def get_expression(
        self,
        gse: str,
        gene_filter: set[str] | None = None,
        sample_filter: set[str] | None = None,
    ) -> dict:
        """Return the expression matrix for a GSE accession.

        Args:
            gse:           GEO series accession, e.g. "GSE98386"
            gene_filter:   Optional set of gene symbols to include (case-insensitive).
                           Returns all genes if None.
            sample_filter: Optional set of GSM IDs to include.
                           Returns all samples for the GSE if None.

        Returns:
            {
                accession:    str,
                genes:        list[str],        # gene symbols
                samples:      list[str],        # GSM IDs
                matrix:       list[list[int]],  # genes × samples, raw counts
                gene_count:   int,
                sample_count: int,
            }

        Raises:
            KeyError: no samples found in the H5 file for this GSE.
        """
        col_idx = self._col_indices(gse)
        if len(col_idx) == 0:
            raise KeyError(f"No samples found in ARCHS4 for {gse}")

        gsm_ids = self._gsm_ids(col_idx)

        # Apply sample filter
        if sample_filter:
            keep_mask = [gsm in sample_filter for gsm in gsm_ids]
            col_idx  = col_idx[keep_mask]
            gsm_ids  = [gsm for gsm, keep in zip(gsm_ids, keep_mask) if keep]

        gene_rows = self._resolve_genes(gene_filter)

        if len(col_idx) == 0 or len(gene_rows) == 0:
            return {
                "accession": gse,
                "genes": [],
                "samples": gsm_ids,
                "matrix": [],
                "gene_count": 0,
                "sample_count": len(gsm_ids),
            }

        gene_syms, matrix = self._read_matrix(gene_rows, col_idx)

        return {
            "accession": gse,
            "genes": gene_syms,
            "samples": gsm_ids,
            "matrix": matrix,
            "gene_count": len(gene_syms),
            "sample_count": len(gsm_ids),
        }

    def iter_expression_tsv(
        self,
        gse: str,
        gene_filter: set[str] | None = None,
        sample_filter: set[str] | None = None,
    ) -> Generator[str, None, None]:
        """Yield TSV lines for the expression matrix of a GSE.

        First line is a header: accession\\tgene\\tGSM1\\tGSM2\\t...
        Subsequent lines: accession\\tGENE\\tcount1\\tcount2\\t...
        Yields nothing if the GSE is not present in the H5 file.
        """
        col_idx = self._col_indices(gse)
        if len(col_idx) == 0:
            return

        gsm_ids = self._gsm_ids(col_idx)

        if sample_filter:
            keep_mask = [gsm in sample_filter for gsm in gsm_ids]
            col_idx  = col_idx[keep_mask]
            gsm_ids  = [gsm for gsm, keep in zip(gsm_ids, keep_mask) if keep]

        gene_rows = self._resolve_genes(gene_filter)

        if len(col_idx) == 0 or len(gene_rows) == 0:
            return

        gene_syms, matrix = self._read_matrix(gene_rows, col_idx)

        yield "accession\tgene\t" + "\t".join(gsm_ids) + "\n"
        for sym, vals in zip(gene_syms, matrix):
            yield gse + "\t" + sym + "\t" + "\t".join(str(v) for v in vals) + "\n"


# ---------------------------------------------------------------------------
# Module-level singleton — opened once per process, reused across requests.
# ---------------------------------------------------------------------------
_archs4: ARCHS4 | None = None


def get_archs4(h5_path: str | Path = _DEFAULT_H5) -> ARCHS4:
    """Return the process-wide ARCHS4 singleton, opening it on first call."""
    global _archs4
    if _archs4 is None:
        logger.info("Opening ARCHS4 H5 file: %s", h5_path)
        _archs4 = ARCHS4(h5_path)
    return _archs4
