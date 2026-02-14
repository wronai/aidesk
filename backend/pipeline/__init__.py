"""
Pipeline package — composable analysis pipeline with SOLID step abstraction.

Re-exports all public symbols for backward compatibility with:
    from pipeline import PipelineContext, PipelineOrchestrator, ...
"""
# Context & profiles
from .context import PipelineProfile, PipelineContext, ProfileSelector

# Protocol
from .protocol import PipelineStep

# Core steps
from .steps_core import (
    ScanWindowsStep,
    DetectActiveWindowStep,
    CaptureScreenStep,
    CropWindowsStep,
    BuildContextStep,
    AnalyzeStep,
    SuggestActionsStep,
    BuildBroadcastStep,
)

# Tier 1 steps
from .steps_tier1 import (
    MultiMonitorStep,
    SemanticMemoryStep,
    ActionTemplateStep,
    OCRPostProcessStep,
    PredictiveStep,
    ClipboardStep,
    ClipboardRelationStep,
)

# Orchestrator & factories
from .orchestrator import (
    ParallelGroup,
    PipelineOrchestrator,
    create_pipeline,
    create_profile_selector,
)

__all__ = [
    # Context
    "PipelineProfile",
    "PipelineContext",
    "ProfileSelector",
    # Protocol
    "PipelineStep",
    # Core steps
    "ScanWindowsStep",
    "DetectActiveWindowStep",
    "CaptureScreenStep",
    "CropWindowsStep",
    "BuildContextStep",
    "AnalyzeStep",
    "SuggestActionsStep",
    "BuildBroadcastStep",
    # Tier 1 steps
    "MultiMonitorStep",
    "SemanticMemoryStep",
    "ActionTemplateStep",
    "OCRPostProcessStep",
    "PredictiveStep",
    "ClipboardStep",
    "ClipboardRelationStep",
    # Orchestrator
    "ParallelGroup",
    "PipelineOrchestrator",
    "create_pipeline",
    "create_profile_selector",
]
