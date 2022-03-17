# cython: language_level=3, boundscheck=True
"""FEC Utils."""

import array
from cpython cimport array
from libc.stdint cimport uint8_t
from libc.stdlib cimport malloc, calloc, free
from libc.string cimport memcpy

from pyremoteplay.fec_utils cimport fec


cdef void check_width(int width, int size):
    if width not in (8, 16, 32):
        raise ValueError("Width must be one of (8, 16, 32)")
    if size % sizeof(long) != 0:
        raise ValueError(f"Size must be divisible by {sizeof(long)}")


cdef int allocate_erasures(int k, int* erasures, erased):
    cdef int i = 0
    cdef int erased_index = 0
    for i in range(len(erased)):
        erasures[i] = erased[i]
    erasures[len(erased)] = -1  # Important. Make last index '-1'
    return 0


cdef int allocate_block_ptrs(int k, int m, int size, array.array data, char **data_ptrs, char **coding_ptrs):
    cdef int i = 0
    for i in range(k + m):
        index = size * i
        if i < k:
            data_ptrs[i] = &data.data.as_chars[index]
        else:
            coding_ptrs[i-k] = &data.data.as_chars[index]
    return 0

cpdef decode(int k, int m, int size, bytes data, missing):
    cdef int width = 8
    check_width(width, size)
    cdef int *matrix = fec.cauchy_original_coding_matrix(k, m, width)
    cdef int *erasures = <int *> calloc(len(missing) + 1, sizeof(int));
    cdef char **data_ptrs = <char **> calloc(k, sizeof(char *))
    cdef char **coding_ptrs = <char **> calloc(m, sizeof(char *))
    cdef array.array data_array = array.array("I", data)

    allocate_erasures(k, erasures, missing)
    allocate_block_ptrs(k, m, size, data_array, data_ptrs, coding_ptrs)
    result = fec.jerasure_matrix_decode(k, m, width, matrix, 0, erasures, data_ptrs, coding_ptrs, size)

    free(data_ptrs)
    free(coding_ptrs)
    free(erasures)

    if result < 0:
        return b""
    return data_array.tobytes()
