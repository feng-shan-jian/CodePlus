"""Explicit input selection and immutable original checkpoints."""

from .records import InputSelection, InputManifest, InputEntry, InputCheckpoint, RawSnapshot
from .selection import select_inputs
from .capture import capture_inputs, read_input, verify_inputs
from .processing import process_inputs, read_processed
from .mutations import begin_changes, build_changes, process_changes, retry_failed, summary as mutation_summary

__all__ = ['InputSelection', 'InputManifest', 'InputEntry', 'InputCheckpoint', 'RawSnapshot',
           'select_inputs', 'capture_inputs', 'read_input', 'verify_inputs', 'process_inputs', 'read_processed',
           'begin_changes', 'build_changes', 'process_changes', 'retry_failed', 'mutation_summary']
