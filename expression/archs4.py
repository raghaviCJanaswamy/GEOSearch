"""
ARCHS4 H5 expression matrix accessor — multi-species support.

Scans expressiondata/*.h5 (or a legacy single file) and routes GSE lookups
to whichever file contains that accession.

H5 layout (per file):
    data/expression               uint32  (n_genes × n_samples)
    meta/genes/symbol             object  (n_genes,)
    meta/samples/geo_accession    object  (n_samples,)  — GSM IDs
    meta/samples/series_id        object  (n_samples,)  — GSE IDs
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator

import h5py
import numpy as np

logger = logging.getLogger(__name__)

# Default directory containing *.h5 files; falls back to legacy single file
_DEFAULT_DIR = Path(__file__).parent.parent / "expressiondata"
_LEGACY_H5   = Path(__file__).parent.parent / "human_gene_v2.latest.h5"


def _decode(raw) -> str:
    return raw.decode() if isinstance(raw, bytes) else raw


class ARCHS4:
    """Read-only accessor for one ARCHS4 H5 expression file."""

    def __init__(self, h5_path: str | Path) -> None:
        self._path = Path(h5_path)
        if not self._path.exists():
            raise FileNotFoundError(f"ARCHS4 H5 file not found: {self._path}")
        self._f = h5py.File(self._path, "r")

        raw_genes = self._f["meta"]["genes"]["symbol"][:]
        self._gene_symbols: list[str] = [_decode(g) for g in raw_genes]
        self._gene_index: dict[str, int] = {
            sym.upper(): i for i, sym in enumerate(self._gene_symbols)
        }

        raw_series = self._f["meta"]["samples"]["series_id"][:]
        self._series_ids: np.ndarray = np.array([_decode(v) for v in raw_series])

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
        return np.where(self._series_ids == gse)[0]

    def _gsm_ids(self, col_indices: np.ndarray) -> list[str]:
        raw = self._f["meta"]["samples"]["geo_accession"][col_indices]
        return [_decode(v) for v in raw]

    def _resolve_genes(self, gene_filter: set[str] | None) -> list[tuple[str, int]]:
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
        expr = self._f["data"]["expression"]
        col_idx_sorted = np.sort(col_idx)
        col_order = np.argsort(np.argsort(col_idx))

        gene_rows_sorted = sorted(gene_rows, key=lambda x: x[1])
        syms_sorted = [sym for sym, _ in gene_rows_sorted]
        row_indices = [i for _, i in gene_rows_sorted]

        if len(row_indices) == len(self._gene_symbols):
            block = expr[:, col_idx_sorted]
            return syms_sorted, block[:, col_order].tolist()

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

    @property
    def gene_count(self) -> int:
        return len(self._gene_symbols)

    @property
    def sample_count(self) -> int:
        return len(self._series_ids)

    @property
    def series_index(self) -> set[str]:
        return set(self._series_ids.tolist())

    def has_gse(self, gse: str) -> bool:
        return bool(np.any(self._series_ids == gse))

    def gse_info(self, gse: str) -> dict | None:
        col_idx = self._col_indices(gse)
        if len(col_idx) == 0:
            return None
        return {
            "accession": gse,
            "gene_count": len(self._gene_symbols),
            "sample_count": int(len(col_idx)),
            "file_format": "h5",
            "source_file": self._path.name,
        }

    def batch_gse_info(self, gse_list: list[str]) -> dict[str, dict]:
        if not gse_list:
            return {}
        wanted = set(gse_list)
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
                "source_file": self._path.name,
            }
            for gse, cnt in zip(unique.tolist(), counts.tolist())
        }

    def get_expression(
        self,
        gse: str,
        gene_filter: set[str] | None = None,
        sample_filter: set[str] | None = None,
    ) -> dict:
        col_idx = self._col_indices(gse)
        if len(col_idx) == 0:
            raise KeyError(f"No samples found in ARCHS4 for {gse}")

        gsm_ids = self._gsm_ids(col_idx)

        if sample_filter:
            keep_mask = [gsm in sample_filter for gsm in gsm_ids]
            col_idx = col_idx[keep_mask]
            gsm_ids = [gsm for gsm, keep in zip(gsm_ids, keep_mask) if keep]

        gene_rows = self._resolve_genes(gene_filter)

        if len(col_idx) == 0 or len(gene_rows) == 0:
            return {
                "accession": gse,
                "genes": [],
                "samples": gsm_ids,
                "matrix": [],
                "gene_count": 0,
                "sample_count": len(gsm_ids),
                "source_file": self._path.name,
            }

        gene_syms, matrix = self._read_matrix(gene_rows, col_idx)
        return {
            "accession": gse,
            "genes": gene_syms,
            "samples": gsm_ids,
            "matrix": matrix,
            "gene_count": len(gene_syms),
            "sample_count": len(gsm_ids),
            "source_file": self._path.name,
        }

    def iter_expression_tsv(
        self,
        gse: str,
        gene_filter: set[str] | None = None,
        sample_filter: set[str] | None = None,
    ) -> Generator[str, None, None]:
        col_idx = self._col_indices(gse)
        if len(col_idx) == 0:
            return

        gsm_ids = self._gsm_ids(col_idx)

        if sample_filter:
            keep_mask = [gsm in sample_filter for gsm in gsm_ids]
            col_idx = col_idx[keep_mask]
            gsm_ids = [gsm for gsm, keep in zip(gsm_ids, keep_mask) if keep]

        gene_rows = self._resolve_genes(gene_filter)

        if len(col_idx) == 0 or len(gene_rows) == 0:
            return

        gene_syms, matrix = self._read_matrix(gene_rows, col_idx)

        yield "accession\tgene\t" + "\t".join(gsm_ids) + "\n"
        for sym, vals in zip(gene_syms, matrix):
            yield gse + "\t" + sym + "\t" + "\t".join(str(v) for v in vals) + "\n"


# ---------------------------------------------------------------------------
# Multi-file manager — routes GSE lookups across all H5 files in expressiondata/
# ---------------------------------------------------------------------------

class ARCHS4Manager:
    """Loads all *.h5 files from a directory and routes lookups to the right one."""

    def __init__(self, data_dir: str | Path = _DEFAULT_DIR) -> None:
        self._dir = Path(data_dir)
        self._files: list[ARCHS4] = []

        h5_paths = sorted(self._dir.glob("*.h5")) if self._dir.exists() else []

        # Fallback: legacy single-file at project root
        if not h5_paths and _LEGACY_H5.exists():
            logger.warning("expressiondata/ not found — falling back to %s", _LEGACY_H5)
            h5_paths = [_LEGACY_H5]

        for p in h5_paths:
            try:
                logger.info("Loading ARCHS4 H5: %s", p.name)
                self._files.append(ARCHS4(p))
            except Exception as e:
                logger.error("Failed to open %s: %s", p, e)

        logger.info(
            "ARCHS4Manager: loaded %d file(s): %s",
            len(self._files),
            [f._path.name for f in self._files],
        )

    def _find(self, gse: str) -> ARCHS4 | None:
        """Return the first H5 file that contains this GSE, or None."""
        for f in self._files:
            if f.has_gse(gse):
                return f
        return None

    def has_gse(self, gse: str) -> bool:
        return self._find(gse) is not None

    def gse_info(self, gse: str) -> dict | None:
        f = self._find(gse)
        return f.gse_info(gse) if f else None

    def batch_gse_info(self, gse_list: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for f in self._files:
            result.update(f.batch_gse_info(gse_list))
        return result

    def get_expression(
        self,
        gse: str,
        gene_filter: set[str] | None = None,
        sample_filter: set[str] | None = None,
    ) -> dict:
        f = self._find(gse)
        if f is None:
            raise KeyError(f"No samples found in any ARCHS4 file for {gse}")
        return f.get_expression(gse, gene_filter, sample_filter)

    def iter_expression_tsv(
        self,
        gse: str,
        gene_filter: set[str] | None = None,
        sample_filter: set[str] | None = None,
    ) -> Generator[str, None, None]:
        f = self._find(gse)
        if f is None:
            return
        yield from f.iter_expression_tsv(gse, gene_filter, sample_filter)

    @property
    def loaded_files(self) -> list[str]:
        return [f._path.name for f in self._files]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_manager: ARCHS4Manager | None = None


def get_archs4(data_dir: str | Path = _DEFAULT_DIR) -> ARCHS4Manager:
    """Return the process-wide ARCHS4Manager singleton."""
    global _manager
    if _manager is None:
        _manager = ARCHS4Manager(data_dir)
    return _manager
