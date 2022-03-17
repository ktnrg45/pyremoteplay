# cython: language_level=3, boundscheck=True
"""Definitions for fec."""

cdef extern from "jerasure.h":
    cdef int jerasure_matrix_decode(int k, int m, int w, int *matrix, int row_k_ones, int *erasures, char **data_ptrs, char **coding_ptrs, int size)

cdef extern from "cauchy.h":
    cdef int* cauchy_original_coding_matrix(int k, int m, int w)